"""
Single-image annotation routes
Separate from dual annotations for clearer data management.
"""
from fastapi import APIRouter, Depends, status, Query
from typing import List
from bson import ObjectId
from datetime import datetime

from app.schemas import SingleAnnotationCreate, SingleAnnotationResponse
from app.db.mongodb import get_single_annotations_collection, get_images_collection
from app.utils.security import get_current_user
from app.services.resource_helpers import get_owned_resource

router = APIRouter(prefix="/annotations/single", tags=["single-annotations"])


@router.post("", response_model=SingleAnnotationResponse, status_code=status.HTTP_201_CREATED)
def create_single_annotation(
    annotation_data: SingleAnnotationCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a new single-image annotation.
    
    Args:
        annotation_data: Annotation data (image_id, text, coords)
        current_user: Current authenticated user
        
    Returns:
        SingleAnnotationResponse with annotation info
        
    Raises:
        HTTP 404: If image not found
        HTTP 403: If image doesn't belong to user
    """
    user_id_str = str(current_user["_id"])
    
    # Verify image exists and belongs to user
    get_owned_resource(
        get_images_collection,
        annotation_data.image_id,
        user_id_str,
        "Image"
    )
    
    # Create annotation document
    annotations_col = get_single_annotations_collection()
    annotation_doc = {
        "user_id": user_id_str,
        "image_id": annotation_data.image_id,
        "text": annotation_data.text,
        "coords": annotation_data.coords.dict(exclude_none=True),
        "type": annotation_data.type or "manipulation",
        "shape_type": annotation_data.shape_type or "rectangle",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    result = annotations_col.insert_one(annotation_doc)
    annotation_doc["_id"] = str(result.inserted_id)
    
    return SingleAnnotationResponse(**annotation_doc)


@router.get("", response_model=List[SingleAnnotationResponse])
def list_single_annotations(
    image_id: str = Query(..., description="Image ID to get annotations for"),
    current_user: dict = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """
    Get single-image annotations for a specific image.
    
    Args:
        image_id: Image ID to get annotations for
        current_user: Current authenticated user
        limit: Maximum number of annotations to return
        offset: Number of annotations to skip
        
    Returns:
        List of SingleAnnotationResponse objects
    """
    annotations_col = get_single_annotations_collection()
    user_id_str = str(current_user["_id"])
    
    # Build query
    query = {
        "user_id": user_id_str,
        "image_id": image_id
    }
    
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
        responses.append(SingleAnnotationResponse(**anno))
    
    return responses


@router.get("/{annotation_id}", response_model=SingleAnnotationResponse)
def get_single_annotation(
    annotation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific single-image annotation.
    
    Args:
        annotation_id: Annotation ID
        current_user: Current authenticated user
        
    Returns:
        SingleAnnotationResponse
        
    Raises:
        HTTP 404: If annotation not found
        HTTP 403: If annotation doesn't belong to user
    """
    user_id_str = str(current_user["_id"])
    
    annotation = get_owned_resource(
        get_single_annotations_collection,
        annotation_id,
        user_id_str,
        "Single Annotation"
    )
    
    annotation["_id"] = str(annotation["_id"])
    return SingleAnnotationResponse(**annotation)


@router.delete("/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_single_annotation(
    annotation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete a single-image annotation.
    
    Args:
        annotation_id: Annotation ID to delete
        current_user: Current authenticated user
        
    Raises:
        HTTP 404: If annotation not found
        HTTP 403: If annotation doesn't belong to user
    """
    user_id_str = str(current_user["_id"])
    
    # Verify annotation exists and belongs to user
    get_owned_resource(
        get_single_annotations_collection,
        annotation_id,
        user_id_str,
        "Single Annotation"
    )
    
    annotations_col = get_single_annotations_collection()
    annotations_col.delete_one({
        "_id": ObjectId(annotation_id),
        "user_id": user_id_str
    })
