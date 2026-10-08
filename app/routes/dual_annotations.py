"""
Dual-image (cross-image) annotation routes
Separate from single annotations for clearer data management.
"""
from fastapi import APIRouter, Depends, status, Query, HTTPException
from typing import List, Optional
from bson import ObjectId
from datetime import datetime, timezone

from app.schemas import (
    DualAnnotationCreate, 
    DualAnnotationResponse, 
    DualAnnotationBatchCreate,
    DualAnnotationUpdate
)
from app.db.mongodb import get_dual_annotations_collection, get_images_collection
from app.utils.security import get_current_user
from app.services.resource_helpers import get_owned_resource

router = APIRouter(prefix="/annotations/dual", tags=["dual-annotations"])


@router.post("", response_model=DualAnnotationResponse, status_code=status.HTTP_201_CREATED)
def create_dual_annotation(
    annotation_data: DualAnnotationCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new dual-image annotation.
    
    Args:
        annotation_data: Annotation data with source/target image IDs and link_id
        current_user: Current authenticated user
        
    Returns:
        DualAnnotationResponse with annotation info
        
    Raises:
        HTTP 404: If image not found
        HTTP 403: If image doesn't belong to user
    """
    user_id_str = str(current_user["_id"])
    
    # Verify both images exist and belong to user
    get_owned_resource(
        get_images_collection,
        annotation_data.source_image_id,
        user_id_str,
        "Source Image"
    )
    get_owned_resource(
        get_images_collection,
        annotation_data.target_image_id,
        user_id_str,
        "Target Image"
    )
    
    # Create annotation document
    annotations_col = get_dual_annotations_collection()
    annotation_doc = {
        "user_id": user_id_str,
        "source_image_id": annotation_data.source_image_id,
        "target_image_id": annotation_data.target_image_id,
        "link_id": annotation_data.link_id,
        "coords": annotation_data.coords.model_dump(exclude_none=True),
        "pair_name": annotation_data.pair_name,
        "pair_color": annotation_data.pair_color,
        "text": annotation_data.text,
        "shape_type": annotation_data.shape_type or "rectangle",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc)
    }
    
    result = annotations_col.insert_one(annotation_doc)
    annotation_doc["_id"] = str(result.inserted_id)
    
    return DualAnnotationResponse(**annotation_doc)


@router.post("/batch", response_model=List[DualAnnotationResponse], status_code=status.HTTP_201_CREATED)
def create_dual_annotations_batch(
    batch_data: DualAnnotationBatchCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create multiple dual-image annotations at once.
    
    Args:
        batch_data: List of annotation data
        current_user: Current authenticated user
        
    Returns:
        List of DualAnnotationResponse with created annotations
    """
    user_id_str = str(current_user["_id"])
    annotations_col = get_dual_annotations_collection()
    images_col = get_images_collection()
    
    # Collect unique image IDs to verify
    unique_image_ids = set()
    for ann_data in batch_data.annotations:
        unique_image_ids.add(ann_data.source_image_id)
        unique_image_ids.add(ann_data.target_image_id)
    
    # Verify all images exist and belong to user
    for img_id in unique_image_ids:
        try:
            img = images_col.find_one({
                "_id": ObjectId(img_id),
                "user_id": user_id_str
            })
            if not img:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Image {img_id} not found or not owned by user"
                )
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid image ID: {img_id}"
            )
    
    # Create all annotations
    created_annotations = []
    for ann_data in batch_data.annotations:
        annotation_doc = {
            "user_id": user_id_str,
            "source_image_id": ann_data.source_image_id,
            "target_image_id": ann_data.target_image_id,
            "link_id": ann_data.link_id,
            "coords": ann_data.coords.model_dump(exclude_none=True),
            "pair_name": ann_data.pair_name,
            "pair_color": ann_data.pair_color,
            "text": ann_data.text,
            "shape_type": ann_data.shape_type or "rectangle",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc)
        }
        
        result = annotations_col.insert_one(annotation_doc)
        annotation_doc["_id"] = str(result.inserted_id)
        created_annotations.append(DualAnnotationResponse(**annotation_doc))
    
    return created_annotations


@router.get("/linked-images/{image_id}", response_model=List[str])
def get_dual_linked_images(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get IDs of images linked to the specified image via dual annotations.
    Returns a distinct list of linked image IDs.
    """
    annotations_col = get_dual_annotations_collection()
    user_id_str = str(current_user["_id"])
    
    # Find images where this image is the source
    pipeline_source = [
        {
            "$match": {
                "source_image_id": image_id,
                "user_id": user_id_str
            }
        },
        {"$group": {"_id": "$target_image_id"}}
    ]
    
    # Find images where this image is the target
    pipeline_target = [
        {
            "$match": {
                "target_image_id": image_id,
                "user_id": user_id_str
            }
        },
        {"$group": {"_id": "$source_image_id"}}
    ]
    
    cursor_source = annotations_col.aggregate(pipeline_source)
    cursor_target = annotations_col.aggregate(pipeline_target)
    
    linked_ids = set()
    for doc in cursor_source:
        if doc["_id"]:
            linked_ids.add(doc["_id"])
    for doc in cursor_target:
        if doc["_id"]:
            linked_ids.add(doc["_id"])
    
    return list(linked_ids)


@router.get("", response_model=List[DualAnnotationResponse])
def list_dual_annotations(
    source_image_id: str = Query(..., description="Source image ID to get annotations for"),
    target_image_id: Optional[str] = Query(None, description="Optional target image ID to filter by"),
    current_user: dict = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """
    Get dual-image annotations for a specific source image.
    Optionally filter by target image ID.
    
    Args:
        source_image_id: Source image ID to get annotations for
        target_image_id: Optional target image ID to filter by
        current_user: Current authenticated user
        limit: Maximum number of annotations to return
        offset: Number of annotations to skip
        
    Returns:
        List of DualAnnotationResponse objects
    """
    annotations_col = get_dual_annotations_collection()
    user_id_str = str(current_user["_id"])
    
    # Build query
    query = {
        "user_id": user_id_str,
        "source_image_id": source_image_id
    }
    if target_image_id:
        query["target_image_id"] = target_image_id
    
    # Get annotations
    annotations = list(
        annotations_col.find(query)
        .sort("created_at", -1)
        .skip(offset)
        .limit(limit)
    )
    
    # Convert ObjectId to string
    responses = []
    for anno in annotations:
        anno["_id"] = str(anno["_id"])
        responses.append(DualAnnotationResponse(**anno))
    
    return responses


@router.get("/{annotation_id}", response_model=DualAnnotationResponse)
def get_dual_annotation(
    annotation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific dual-image annotation.
    """
    user_id_str = str(current_user["_id"])
    
    annotation = get_owned_resource(
        get_dual_annotations_collection,
        annotation_id,
        user_id_str,
        "Dual Annotation"
    )
    
    annotation["_id"] = str(annotation["_id"])
    return DualAnnotationResponse(**annotation)


@router.delete("/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dual_annotation(
    annotation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a dual-image annotation.
    """
    user_id_str = str(current_user["_id"])
    
    # Verify annotation exists and belongs to user
    get_owned_resource(
        get_dual_annotations_collection,
        annotation_id,
        user_id_str,
        "Dual Annotation"
    )
    
    annotations_col = get_dual_annotations_collection()
    annotations_col.delete_one({
        "_id": ObjectId(annotation_id),
        "user_id": user_id_str
    })


@router.put("/{annotation_id}", response_model=DualAnnotationResponse)
def update_dual_annotation(
    annotation_id: str,
    update_data: DualAnnotationUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update an existing dual-image annotation.
    Supports partial updates for coords, pair_name, pair_color, and text.
    """
    user_id_str = str(current_user["_id"])
    
    # Verify annotation exists and belongs to user
    existing = get_owned_resource(
        get_dual_annotations_collection,
        annotation_id,
        user_id_str,
        "Dual Annotation"
    )
    
    # Build update document with only provided fields
    update_fields = {}
    if update_data.coords is not None:
        update_fields["coords"] = update_data.coords.model_dump(exclude_none=True)
    if update_data.pair_name is not None:
        update_fields["pair_name"] = update_data.pair_name
    if update_data.pair_color is not None:
        update_fields["pair_color"] = update_data.pair_color
    if update_data.text is not None:
        update_fields["text"] = update_data.text
    
    if not update_fields:
        # No fields to update, return existing
        existing["_id"] = str(existing["_id"])
        return DualAnnotationResponse(**existing)
    
    update_fields["updated_at"] = datetime.now(timezone.utc)
    
    annotations_col = get_dual_annotations_collection()
    annotations_col.update_one(
        {"_id": ObjectId(annotation_id), "user_id": user_id_str},
        {"$set": update_fields}
    )
    
    # Return updated annotation
    updated = annotations_col.find_one({"_id": ObjectId(annotation_id)})
    updated["_id"] = str(updated["_id"])
    return DualAnnotationResponse(**updated)


@router.delete("/by-link/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dual_annotations_by_link(
    link_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete all dual-image annotations with a specific link_id.
    Useful for deleting an entire linked pair at once.
    """
    user_id_str = str(current_user["_id"])
    annotations_col = get_dual_annotations_collection()
    
    result = annotations_col.delete_many({
        "link_id": link_id,
        "user_id": user_id_str
    })
    
    if result.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No annotations found with link_id: {link_id}"
        )


@router.put("/by-link/{link_id}")
def update_dual_annotations_by_link(
    link_id: str,
    update_data: DualAnnotationUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update all dual-image annotations with a specific link_id.
    Useful for updating pair name/color across all linked annotations.
    Returns the count of updated annotations.
    """
    user_id_str = str(current_user["_id"])
    annotations_col = get_dual_annotations_collection()
    
    # Build update document with only provided fields
    update_fields = {}
    if update_data.pair_name is not None:
        update_fields["pair_name"] = update_data.pair_name
    if update_data.pair_color is not None:
        update_fields["pair_color"] = update_data.pair_color
    if update_data.text is not None:
        update_fields["text"] = update_data.text
    # Note: coords is NOT updated here as each annotation has different coords
    
    if not update_fields:
        return {"updated_count": 0, "message": "No fields to update"}
    
    update_fields["updated_at"] = datetime.now(timezone.utc)
    
    result = annotations_col.update_many(
        {"link_id": link_id, "user_id": user_id_str},
        {"$set": update_fields}
    )
    
    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No annotations found with link_id: {link_id}"
        )
    
    return {
        "updated_count": result.modified_count,
        "message": f"Updated {result.modified_count} annotations"
    }
