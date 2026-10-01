"""
Image service for handling image operations.

Provides business logic for image CRUD operations including cascade deletion and listing.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from bson import ObjectId

from app.db.mongodb import (
    get_dual_annotations_collection,
    get_images_collection,
    get_single_annotations_collection,
)
from app.exceptions import (
    AuthorizationError,
    ValidationError,
)
from app.schemas import JobType, JobStatus
from app.services.job_logger import create_job_log, complete_job
from app.services.deletion_service import delete_images
from app.services.resource_helpers import get_owned_resource

logger = logging.getLogger(__name__)


def delete_image_and_artifacts(
    image_id: str,
    user_id: str
) -> dict:
    """
    Delete an image and everything derived from it (panels, thumbnail,
    annotations, relationships, analyses, CBIR vectors).

    Raises:
        ValidationError: If image ID format is invalid.
        ResourceNotFoundError: If image not found.
        AuthorizationError: If trying to delete extracted images directly.
    """
    img = get_owned_resource(get_images_collection, image_id, user_id, "Image")

    # Extracted images belong to their document
    if img.get("source_type") == "extracted":
        raise AuthorizationError(
            "Cannot delete extracted images directly. Delete the document instead."
        )

    image_name = img.get("original_filename") or img.get("filename") or image_id
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.IMAGE_DELETION,
        title=f"Deleting image: {image_name}",
        input_data={"image_id": image_id, "filename": image_name}
    )

    try:
        result = delete_images([img])
    except Exception as e:
        complete_job(job_id=job_id, user_id=user_id, status=JobStatus.FAILED, errors=[str(e)])
        raise

    result["deleted_id"] = image_id
    complete_job(job_id=job_id, user_id=user_id, status=JobStatus.COMPLETED, output_data=result)
    return result


def build_image_query(
    user_id: str,
    source_type: Optional[str] = None,
    document_id: Optional[str] = None,
    image_type: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    flagged: Optional[bool] = None,
    linked_to_image_id: Optional[str] = None,
    include_annotated: bool = False,
) -> Dict[str, Any]:
    """
    MongoDB filter for a user's images. Shared by the gallery listing and the
    "select all" id listing so both always apply the same filters.

    Raises:
        ValidationError: invalid source_type or date format
    """
    from datetime import datetime

    single_annotations_col = get_single_annotations_collection()
    dual_annotations_col = get_dual_annotations_collection()

    # Validate source_type if provided
    if source_type and source_type not in ["extracted", "uploaded", "panel"]:
        raise ValidationError("source_type must be 'extracted', 'uploaded', or 'panel'")

    # Build query
    query = {"user_id": user_id}
    
    if source_type:
        query["source_type"] = source_type
    
    if document_id:
        query["document_id"] = document_id
    
    # Handle flagged filter with optional include_annotated
    if flagged is True and include_annotated:
        # Get image IDs that have annotations in either collection
        single_annotated_ids = single_annotations_col.distinct("image_id", {"user_id": user_id})
        dual_source_ids = dual_annotations_col.distinct("source_image_id", {"user_id": user_id})
        dual_target_ids = dual_annotations_col.distinct("target_image_id", {"user_id": user_id})
        
        # Combine all annotated image IDs
        all_annotated_ids = set(single_annotated_ids) | set(dual_source_ids) | set(dual_target_ids)
        
        # Use OR condition: flagged OR has annotations
        query["$or"] = [
            {"is_flagged": True},
            {"_id": {"$in": [ObjectId(id) for id in all_annotated_ids if ObjectId.is_valid(id)]}}
        ]
    elif flagged is not None:
        query["is_flagged"] = flagged
    
    # Image type filter - match any of the provided types
    if image_type:
        query["image_type"] = {"$in": image_type}
    
    # Date range filter
    if date_from or date_to:
        date_query = {}
        if date_from:
            try:
                parsed_from = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
                date_query["$gte"] = parsed_from
            except ValueError as e:
                raise ValidationError(f"Invalid date format for date_from: {date_from}. Expected ISO format (e.g., 2025-01-01)") from e
        if date_to:
            try:
                parsed_to = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
                # Set to end of day
                parsed_to = parsed_to.replace(hour=23, minute=59, second=59, microsecond=999999)
                date_query["$lte"] = parsed_to
            except ValueError as e:
                raise ValidationError(f"Invalid date format for date_to: {date_to}. Expected ISO format (e.g., 2025-01-01)") from e
        if date_query:
            query["uploaded_date"] = date_query
    
    # Search filter - case-insensitive regex on filename
    if search:
        query["filename"] = {"$regex": re.escape(search), "$options": "i"}

    # Linked image filter (Dual Annotations)
    if linked_to_image_id:
        dual_col = get_dual_annotations_collection()
        # Find all dual annotations where the given image source OR target
        linked_ids = set()
        cursor = dual_col.find({
            "user_id": user_id,
            "$or": [
                {"source_image_id": linked_to_image_id},
                {"target_image_id": linked_to_image_id}
            ]
        })
        
        for doc in cursor:
            sid = doc.get("source_image_id")
            tid = doc.get("target_image_id")
            # Add the OTHER image ID to the set
            if sid == linked_to_image_id and tid:
                linked_ids.add(tid)
            elif tid == linked_to_image_id and sid:
                linked_ids.add(sid)
        
        # Filter query by these IDs
        # Note: If no linked images, we force an empty match using a non-existent ID or empty list
        linked_oids = [ObjectId(id) for id in linked_ids if ObjectId.is_valid(id)]
        if not linked_oids:
             # Force empty result
             query["_id"] = {"$in": []}
        else:
            # Intersect with existing _id filter if any (unlikely unless searched by ID?)
            # But we must be careful not to overwrite if logic uses _id
            query.setdefault("_id", {})["$in"] = linked_oids
    
    return query


def list_images(
    user_id: str,
    source_type: Optional[str] = None,
    document_id: Optional[str] = None,
    image_type: Optional[List[str]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    flagged: Optional[bool] = None,
    linked_to_image_id: Optional[str] = None,
    include_annotated: bool = False,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "uploaded_date",
    sort_order: int = -1
) -> Dict[str, Any]:
    """
    List images for a user with optional filtering and pagination.
    
    This is the single source of truth for image listing logic.
    Called by route handlers which map results to appropriate API response models.
    
    Args:
        user_id: User ID (as string) who owns the images
        source_type: Optional filter - "extracted" or "uploaded"
        document_id: Optional filter by document ID
        image_type: Optional list of image types/tags to filter by (matches any)
        date_from: Optional ISO date string to filter images uploaded on or after
        date_to: Optional ISO date string to filter images uploaded on or before
        search: Optional search string for filename (case-insensitive)
        flagged: Optional filter by flagged status - True for flagged images only
        include_annotated: If True and flagged=True, also include images with annotations
        limit: Maximum number of images to return
        offset: Number of images to skip
        sort_by: Field to sort by (default: "uploaded_date")
        sort_order: Sort order - 1 for ascending, -1 for descending (default: -1)
        
    Returns:
        Dictionary with keys:
        - images: List of image documents from MongoDB
        - total: Total count of images matching the query
        - returned: Number of images returned in this query
        
    Raises:
        ValidationError: If source_type is invalid or date_from/date_to have invalid ISO format
    """
    images_col = get_images_collection()
    query = build_image_query(
        user_id=user_id,
        source_type=source_type,
        document_id=document_id,
        image_type=image_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        flagged=flagged,
        linked_to_image_id=linked_to_image_id,
        include_annotated=include_annotated,
    )

    # Get total count before pagination
    total_count = images_col.count_documents(query)
    
    # Query images with pagination
    # Query images with pagination - optimize projection to exclude heavy fields
    # Exclude exif_metadata which can be large and isn't needed for the gallery view
    projection = {"exif_metadata": 0}
    
    images = list(
        images_col.find(query, projection)
        .sort(sort_by, sort_order)
        .skip(offset)
        .limit(limit)
    )
    
    # Convert ObjectId to string for each image
    for img in images:
        img["_id"] = str(img["_id"])
    
    return {
        "images": images,
        "total": total_count,
        "returned": len(images)
    }
