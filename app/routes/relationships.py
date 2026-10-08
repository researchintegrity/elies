"""
Image Relationship Routes

REST API endpoints for managing image-to-image relationships.
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List
from bson import ObjectId

from app.utils.security import get_current_user
from app.db.mongodb import get_images_collection
from app.schemas import (
    ImageRelationshipCreate,
    ImageRelationshipResponse,
    RelationshipGraphResponse,
    MessageResponse
)
from app.services import relationship_service

router = APIRouter(
    prefix="/relationships",
    tags=["Image Relationships"]
)


@router.post(
    "",
    response_model=ImageRelationshipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a relationship between two images"
)
def create_relationship(
    request: ImageRelationshipCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create a bidirectional relationship between two images.
    
    - Both images will be automatically flagged if either is flagged
    - Duplicate relationships return the existing one (idempotent)
    - Weight indicates relationship strength (0-1, higher = stronger)
    
    Source types:
    - **manual**: User-created relationship
    - **provenance**: From provenance analysis
    - **cross_copy_move**: From cross-image copy-move detection
    - **similarity**: From CBIR similarity search
    """
    user_id = str(current_user["_id"])
    images_col = get_images_collection()
    
    # Verify both images exist and belong to user
    try:
        img1 = images_col.find_one({
            "_id": ObjectId(request.image1_id),
            "user_id": user_id
        })
        img2 = images_col.find_one({
            "_id": ObjectId(request.image2_id),
            "user_id": user_id
        })
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image ID format"
        )
    
    if not img1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image {request.image1_id} not found"
        )
    if not img2:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Image {request.image2_id} not found"
        )
    
    if request.image1_id == request.image2_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot create relationship between an image and itself"
        )
    
    try:
        relationship = relationship_service.create_relationship(
            user_id=user_id,
            image1_id=request.image1_id,
            image2_id=request.image2_id,
            source_type=request.source_type.value,
            source_analysis_id=request.source_analysis_id,
            weight=request.weight,
            metadata=request.metadata,
            created_by=user_id  # Manual creation by user
        )
        return relationship
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete(
    "/{relationship_id}",
    response_model=MessageResponse,
    summary="Remove a relationship"
)
def remove_relationship(
    relationship_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Remove a relationship by its ID.
    
    Note: This does not unflag the images - they remain flagged.
    """
    user_id = str(current_user["_id"])
    
    try:
        ObjectId(relationship_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid relationship ID format"
        )
    
    deleted = relationship_service.remove_relationship(
        relationship_id=relationship_id,
        user_id=user_id
    )
    
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found"
        )
    
    return {"message": "Relationship removed successfully"}


@router.get(
    "/image/{image_id}",
    response_model=List[ImageRelationshipResponse],
    summary="Get all relationships for an image"
)
def get_relationships_for_image(
    image_id: str,
    include_details: bool = Query(True, description="Include other image details"),
    current_user: dict = Depends(get_current_user)
):
    """
    Get all relationships where this image is involved.
    
    Returns enriched relationships with basic info about the related image
    (filename, is_flagged) when include_details=true.
    """
    user_id = str(current_user["_id"])
    images_col = get_images_collection()
    
    # Verify image exists
    try:
        img = images_col.find_one({
            "_id": ObjectId(image_id),
            "user_id": user_id
        })
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image ID format"
        )
    
    if not img:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )
    
    relationships = relationship_service.get_relationships_for_image(
        image_id=image_id,
        user_id=user_id,
        include_image_details=include_details
    )
    
    return relationships


@router.get(
    "/image/{image_id}/graph",
    response_model=RelationshipGraphResponse,
    summary="Get relationship graph for visualization"
)
def get_relationship_graph(
    image_id: str,
    max_depth: int = Query(5, ge=0, description="Maximum exploration depth (0 = unlimited)"),
    current_user: dict = Depends(get_current_user)
):
    """
    Build a relationship graph starting from the given image.
    
    Uses BFS to explore relationships up to max_depth.
    Returns nodes, all edges, and precomputed Maximum Spanning Tree edges.
    
    Visualization hint:
    - MST edges (is_mst_edge=true) should be rendered darker/thicker
    - Non-MST edges should be rendered lighter/dashed
    """
    user_id = str(current_user["_id"])
    images_col = get_images_collection()
    
    # Verify image exists
    try:
        img = images_col.find_one({
            "_id": ObjectId(image_id),
            "user_id": user_id
        })
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image ID format"
        )
    
    if not img:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )
    
    graph_data = relationship_service.get_relationship_graph(
        image_id=image_id,
        user_id=user_id,
        max_depth=max_depth
    )
    
    return graph_data
