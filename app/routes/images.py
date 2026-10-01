"""
Image upload routes for extracted and user-uploaded image management
"""
import logging
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image

from app.exceptions import ELIESException, StorageQuotaExceededError, ValidationError
from app.config.settings import (
    DEFAULT_THUMBNAIL_SIZE,
    MAX_BATCH_UPLOAD_FILES,
    MAX_IMAGE_PIXELS,
    MAX_SELECT_ALL_IDS,
    THUMBNAIL_JPEG_QUALITY,
)
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import (
    get_images_collection,
    get_indexing_jobs_collection,
)
from app.schemas import (
    BatchUploadResponse,
    ImageResponse,
    ImageTypesUpdateRequest,
    IndexingJobResponse,
    IndexingJobStatus,
    PaginatedImageResponse,
    PanelExtractionInitiationResponse,
    PanelExtractionRequest,
    PanelExtractionStatusResponse,
    JobType,
    JobStatus,
)
from app.services.deletion_service import delete_images
from app.services.storage_service import add_storage, path_size
from app.services.image_service import (
    build_image_query,
    delete_image_and_artifacts,
    list_images as list_images_service,
)
from app.services.job_logger import attach_celery_task, create_job_log, complete_job
from app.services.panel_extraction_service import (
    get_panel_extraction_status,
    get_panels_by_source_image,
    initiate_panel_extraction,
)
from app.services.quota_helpers import augment_list_with_quota, augment_with_quota, image_response
from app.services.resource_helpers import get_owned_resource
from app.services.upload_service import save_uploaded_image
from app.tasks.cbir import cbir_index_image, cbir_update_labels, cbir_index_batch_with_progress
from app.utils.docker_cbir import check_cbir_health
from app.utils.file_storage import (
    get_thumbnail_path,
)
from app.utils.security import get_current_user, get_current_user_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])

# Refuse to decode decompression bombs when generating thumbnails
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _require_cbir_available() -> None:
    """Block uploads while CBIR is down: new images could not be indexed."""
    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        logger.warning("CBIR service unavailable: %s", cbir_message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to upload images at this time. Please try again in a few minutes."
        )


def _discard_new_images(image_ids: List[str], user_id: str) -> None:
    """Remove images created by the current request (records and files)."""
    oids = [ObjectId(image_id) for image_id in image_ids]
    delete_images(list(get_images_collection().find({"_id": {"$in": oids}, "user_id": user_id})))


@router.post("/upload", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
def upload_image(
    file: UploadFile = File(...),
    document_id: str = Query(None, description="Optional document ID if image is related to a document"),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload an image file (for user-uploaded images)

    - Validates the extension, size, quota, optional document ownership and
      that the content is a decodable image
    - Stores the file as images/uploaded/{_id}.{ext}; the client filename is
      kept only as ``original_filename``
    - Queues CBIR indexing

    Raises:
        HTTP 400: Invalid file, image content or document ID
        HTTP 404: Document not found
        HTTP 413: Storage quota would be exceeded
        HTTP 503: CBIR service unavailable
    """
    _require_cbir_available()
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)

    img_record = save_uploaded_image(current_user, file.filename, file.file, document_id=document_id)
    image_id = str(img_record["_id"])

    try:
        cbir_index_image.delay(
            user_id=user_id_str,
            image_id=image_id,
            image_path=img_record["file_path"],
            labels=img_record.get("image_type", [])
        )
    except Exception as e:
        # Log but don't fail the upload if CBIR indexing fails to queue
        logger.warning("Failed to queue CBIR indexing for image %s: %s", image_id, e)

    img_record["_id"] = image_id
    img_record = augment_with_quota(img_record, user_id_str, user_quota)
    return ImageResponse(**img_record)


# ============================================================================
# BATCH UPLOAD AND INDEXING STATUS ENDPOINTS
# ============================================================================

@router.post("/upload/batch", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED)
def upload_images_batch(
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload multiple images in a single request with progress tracking.

    - Validates and saves each image (invalid files are skipped)
    - Stops accepting files once the storage quota is reached
    - Starts a single Celery task to index all images with progress tracking
    - Returns a job_id to poll for indexing progress

    Raises:
        HTTP 400: If no valid images provided
        HTTP 413: If the first image already exceeds the storage quota
    """
    user_id_str = str(current_user["_id"])
    _require_cbir_available()

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image file is required"
        )
    if len(files) > MAX_BATCH_UPLOAD_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many files in one batch (maximum {MAX_BATCH_UPLOAD_FILES})"
        )

    uploaded_images = []
    for file in files:
        try:
            img_record = save_uploaded_image(current_user, file.filename, file.file)
        except StorageQuotaExceededError as e:
            if not uploaded_images:
                raise
            logger.warning("Stopping batch upload at %s: %s", file.filename, e)
            break
        except ValidationError as e:
            logger.warning("Skipping invalid image %s: %s", file.filename, e)
            continue
        uploaded_images.append({
            "image_id": str(img_record["_id"]),
            "image_path": img_record["file_path"],
            "labels": []
        })

    if not uploaded_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid images could be uploaded"
        )
    new_image_ids = [img["image_id"] for img in uploaded_images]

    # Create job log entry for the jobs dashboard (tracks the batch upload)
    main_job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.BATCH_UPLOAD,
        title=f"Batch Upload ({len(uploaded_images)} images)",
        input_data={"image_count": len(uploaded_images), "image_ids": new_image_ids}
    )

    job_id = f"idx_{uuid.uuid4().hex}"
    now = datetime.now(timezone.utc)
    job_doc = {
        "_id": job_id,
        "user_id": user_id_str,
        "status": IndexingJobStatus.PENDING.value,
        "total_images": len(uploaded_images),
        "processed_images": 0,
        "indexed_images": 0,
        "failed_images": 0,
        "progress_percent": 0.0,
        "current_step": "Queued for indexing",
        "errors": [],
        "created_at": now,
        "updated_at": now,
        "completed_at": None
    }

    try:
        get_indexing_jobs_collection().insert_one(job_doc)
        task = cbir_index_batch_with_progress.delay(
            job_id=job_id,
            user_id=user_id_str,
            image_items=uploaded_images,
            main_job_id=main_job_id
        )
    except Exception as e:
        logger.error("Failed to start batch indexing for user %s: %s", user_id_str, e)
        _discard_new_images(new_image_ids, user_id_str)
        complete_job(main_job_id, user_id_str, JobStatus.FAILED, errors=["Failed to queue indexing; upload rolled back"])
        get_indexing_jobs_collection().delete_one({"_id": job_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start indexing for uploaded images. Upload has been rolled back.",
        )

    attach_celery_task(main_job_id, task.id)

    return BatchUploadResponse(
        job_id=job_id,
        uploaded_count=len(uploaded_images),
        image_ids=new_image_ids,
        message=f"{len(uploaded_images)} images uploaded, indexing in progress"
    )


@router.get("/indexing-status/{job_id}", response_model=IndexingJobResponse)
def get_indexing_status(
    job_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get the status of a batch indexing job.
    
    Poll this endpoint to track progress of batch image indexing.
    
    Args:
        job_id: The job ID returned from batch upload
        current_user: Current authenticated user
        
    Returns:
        IndexingJobResponse with current progress
        
    Raises:
        HTTP 404: If job not found or doesn't belong to user
    """
    user_id_str = str(current_user["_id"])
    jobs_col = get_indexing_jobs_collection()
    
    job = jobs_col.find_one({"_id": job_id, "user_id": user_id_str})
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Indexing job not found"
        )
    
    return IndexingJobResponse(
        job_id=job["_id"],
        user_id=job["user_id"],
        status=IndexingJobStatus(job["status"]),
        total_images=job["total_images"],
        processed_images=job.get("processed_images", 0),
        indexed_images=job.get("indexed_images", 0),
        failed_images=job.get("failed_images", 0),
        progress_percent=job.get("progress_percent", 0.0),
        current_step=job.get("current_step", ""),
        errors=job.get("errors", []),
        created_at=job["created_at"],
        updated_at=job.get("updated_at", job["created_at"]),
        completed_at=job.get("completed_at")
    )


@router.get("", response_model=PaginatedImageResponse)
def list_images(
    current_user: dict = Depends(get_current_user),
    source_type: str = Query(None, description="Filter by 'extracted' or 'uploaded'"),
    document_id: str = Query(None, description="Filter by document ID"),
    image_type: str = Query(None, description="Comma-separated list of image types/tags to filter"),
    date_from: str = Query(None, description="Filter images from this date (ISO format YYYY-MM-DD)"),
    date_to: str = Query(None, description="Filter images until this date (ISO format YYYY-MM-DD)"),
    search: str = Query(None, description="Search in filename (case-insensitive)"),
    flagged: Optional[bool] = Query(None, description="Filter by flagged status - True for flagged images only"),
    linked_to_image_id: Optional[str] = Query(None, description="Filter images linked to this image ID via dual annotations"),
    include_annotated: bool = Query(False, description="If true with flagged=true, also include images with annotations"),
    page: int = Query(1, ge=1, description="Page number (1-indexed). default: 1"),
    per_page: int = Query(24, ge=1, le=100, description="Number of items per page (1-100, default 24)")
):
    """
    List all images uploaded by current user with pagination and filtering.
    
    Returns PaginatedImageResponse with metadata (total, total_pages, has_next, has_prev) for efficient gallery pagination.
    
    Args:
        current_user: Current authenticated user
        source_type: Optional filter - 'extracted', 'uploaded', or 'panel'
        document_id: Optional filter by document ID
        image_type: Optional comma-separated list of image types/tags to filter
        date_from: Optional ISO date string for start of date range
        date_to: Optional ISO date string for end of date range
        search: Optional search string for filename
        flagged: Optional filter by flagged status
        linked_to_image_id: Optional filter for images linked to this ID via dual annotations
        page: Page number (1-indexed).
        per_page: Number of items per page (default: 24, max: 100)
        
    Returns:
        PaginatedImageResponse
    """
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    # Pagination
    actual_offset = (page - 1) * per_page
    actual_limit = per_page
    
    # Parse image_type from comma-separated string
    parsed_image_type = [t.strip() for t in image_type.split(",")] if image_type else None
    
    # Use service to get images with all filter parameters
    result = list_images_service(
        user_id=user_id_str,
        source_type=source_type,
        document_id=document_id,
        image_type=parsed_image_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        flagged=flagged,
        linked_to_image_id=linked_to_image_id,
        include_annotated=include_annotated,
        limit=actual_limit,
        offset=actual_offset
    )
    
    # Map to response models with quota info (OPTIMIZED: calculate quota once)
    augmented_images = augment_list_with_quota(result["images"], user_id_str, user_quota)
    responses = [ImageResponse(**img) for img in augmented_images]
    
    # Return paginated response with metadata
    total = result["total"]
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    
    return PaginatedImageResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


@router.get("/tags", response_model=List[str])
def get_all_tags(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all unique image tags/categories for the current user.
    
    Uses MongoDB's distinct() for efficient retrieval without loading image data.
    This is much more efficient than fetching images and extracting tags client-side.
    
    Returns:
        List of unique tag strings, sorted alphabetically
    """
    user_id_str = str(current_user["_id"])
    images_col = get_images_collection()
    
    # Use MongoDB distinct() for efficient unique value retrieval
    # This queries the database index directly without loading documents
    tags = images_col.distinct("image_type", {"user_id": user_id_str})
    
    # Filter out None/empty values and sort
    unique_tags = sorted([tag for tag in tags if tag])
    
    return unique_tags


@router.get("/ids", response_model=dict)
def get_all_image_ids(
    image_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = Query(None, max_length=200),
    source_type: Optional[str] = None,
    document_id: Optional[str] = None,
    flagged: Optional[bool] = None,
    linked_to_image_id: Optional[str] = None,
    include_annotated: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """
    Get the IDs of all images matching the gallery filters ("Select All").

    Accepts the same filters as GET /images and applies them identically.
    At most MAX_SELECT_ALL_IDS ids are returned; ``truncated`` tells whether
    more matched.

    Returns:
        {"ids": ["id1", "id2", ...], "count": N, "truncated": bool}
    """
    parsed_image_type = [t.strip() for t in image_type.split(",") if t.strip()] if image_type else None
    query = build_image_query(
        user_id=str(current_user["_id"]),
        source_type=None if source_type == "all" else source_type,
        document_id=document_id,
        image_type=parsed_image_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
        flagged=flagged,
        linked_to_image_id=linked_to_image_id,
        include_annotated=include_annotated,
    )

    cursor = get_images_collection().find(query, {"_id": 1}).limit(MAX_SELECT_ALL_IDS + 1)
    ids = [str(doc["_id"]) for doc in cursor]
    truncated = len(ids) > MAX_SELECT_ALL_IDS
    ids = ids[:MAX_SELECT_ALL_IDS]

    return {"ids": ids, "count": len(ids), "truncated": truncated}


@router.get("/{image_id}", response_model=ImageResponse)
def get_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific image by ID
    
    Args:
        image_id: Image ID
        current_user: Current authenticated user
        
    Returns:
        ImageResponse with storage quota info
    """
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    img = get_owned_resource(
        get_images_collection,
        image_id,
        user_id_str,
        "Image"
    )
    
    img["_id"] = image_id
    
    # Add quota information
    img = augment_with_quota(img, user_id_str, user_quota)
    
    return ImageResponse(**img)


@router.get("/{image_id}/download")
def download_image(
    image_id: str,
    current_user: dict = Depends(get_current_user_media)
):
    """
    Download an image file
    
    Args:
        image_id: Image ID
        current_user: Current authenticated user
        
    Returns:
        FileResponse with image file
    """
    user_id_str = str(current_user["_id"])
    
    # Verify image belongs to user
    img = get_owned_resource(
        get_images_collection,
        image_id,
        user_id_str,
        "Image"
    )
    
    # Check if file exists - resolve workspace path to actual filesystem path
    file_path = img["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk"
        )
    
    # Determine media type from file extension
    file_ext = Path(file_path).suffix.lower()
    media_type_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp"
    }
    media_type = media_type_map.get(file_ext, "application/octet-stream")
    
    # Return file
    return FileResponse(
        path=file_path,
        filename=img["filename"],
        media_type=media_type
    )


@router.get("/{image_id}/thumbnail")
def get_image_thumbnail(
    image_id: str,
    current_user: dict = Depends(get_current_user_media)
):
    """
    Get a thumbnail version of an image
    
    - Checks if thumbnail exists on disk
    - If not, generates it from original image
    - Returns cached thumbnail
    
    Args:
        image_id: Image ID
        current_user: Current authenticated user
        
    Returns:
        FileResponse with thumbnail image
    """
    user_id_str = str(current_user["_id"])
    
    # Verify image belongs to user
    img = get_owned_resource(
        get_images_collection,
        image_id,
        user_id_str,
        "Image"
    )
    
    # Check if thumbnail already exists
    thumb_path = get_thumbnail_path(user_id_str, image_id)
    
    if not thumb_path.exists():
        # Generate thumbnail
        file_path = img["file_path"]
        if not Path(file_path).exists():
             raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Original file not found"
            )
            
        try:
            # Open original image
            with Image.open(file_path) as image:
                # Convert to RGB if necessary (e.g. for RGBA/P PNGs saved as JPG)
                if image.mode in ('RGBA', 'P'):
                    image = image.convert('RGB')
                
                # Resize keeping aspect ratio using configured thumbnail size
                image.thumbnail(DEFAULT_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
                
                # Save as JPEG with configured quality
                image.save(thumb_path, "JPEG", quality=THUMBNAIL_JPEG_QUALITY)
            add_storage(user_id_str, path_size(thumb_path))
                
        except Exception as e:
            # If thumbnail generation fails, fallback to original (pass-through)
            logger.warning("Thumbnail generation failed for image %s: %s", image_id, e)
            # Fallback to download_image logic
            return download_image(image_id, current_user)

    # Return thumbnail
    return FileResponse(
        path=thumb_path,
        filename=f"thumb_{img['filename']}",
        media_type="image/jpeg"
    )


@router.patch("/{image_id}/flag", response_model=ImageResponse)
def toggle_image_flag(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Toggle the flagged status of an image.
    
    Flagged images are marked as suspicious for later review.
    
    Args:
        image_id: Image ID to toggle flag status
        current_user: Current authenticated user
        
    Returns:
        Updated ImageResponse with new is_flagged value
    """
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    try:
        img_oid = ObjectId(image_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image ID format"
        )
    
    images_col = get_images_collection()
    
    # Find the image
    img = images_col.find_one({
        "_id": img_oid,
        "user_id": user_id_str
    })
    
    if not img:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found"
        )
    
    # Toggle the flag
    new_flag_status = not img.get("is_flagged", False)
    
    # Update in database
    images_col.update_one(
        {"_id": img_oid},
        {"$set": {"is_flagged": new_flag_status}}
    )
    
    # Get updated image
    img = images_col.find_one({"_id": img_oid})
    img["_id"] = str(img["_id"])
    img = augment_with_quota(img, user_id_str, user_quota)
    
    return ImageResponse(**img)


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete an image file (user-uploaded only)
    
    Args:
        image_id: Image ID
        current_user: Current authenticated user
    """
    delete_image_and_artifacts(
        image_id=image_id,
        user_id=str(current_user["_id"])
    )


# ============================================================================
# Panel Extraction Endpoints
# ============================================================================

@router.post(
    "/extract-panels",
    response_model=PanelExtractionInitiationResponse,
    status_code=status.HTTP_202_ACCEPTED
)
def initiate_panel_extraction_endpoint(
    request: PanelExtractionRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Initiate panel extraction from selected images
    
    Queues a Celery task to extract individual panels from the provided images.
    Returns a task_id for polling extraction status.
    
    - Validates all image IDs belong to current user
    - Validates images are extractable (extracted or uploaded type)
    - Queues Celery task for asynchronous processing
    - Returns task ID for status polling
    
    Args:
        request: Panel extraction request with image_ids
        current_user: Current authenticated user
        
    Returns:
        PanelExtractionInitiationResponse with task_id
        
    Raises:
        HTTP 400: If validation fails
        HTTP 404: If image not found
    """
    user_id = current_user.get("_id")
    
    # Pre-flight CBIR health check - block extraction if CBIR is unavailable
    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        logger.warning("CBIR service unavailable: %s", cbir_message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to upload images at this time. Please try again in a few minutes."
        )
    
    # Validate request
    if not request.image_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image ID is required"
        )
    
    # Initiate extraction
    result = initiate_panel_extraction(
        image_ids=request.image_ids,
        user_id=str(user_id)
    )
    
    return PanelExtractionInitiationResponse(
        task_id=result["task_id"],
        status=result["status"],
        image_ids=result["image_ids"],
        message=result["message"]
    )


@router.get(
    "/extract-panels/status/{task_id}",
    response_model=PanelExtractionStatusResponse,
    status_code=status.HTTP_200_OK
)
def get_panel_extraction_status_endpoint(
    task_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get status of a panel extraction task
    
    Polls for the status of a queued or running panel extraction task.
    When completed, returns the list of extracted panels.
    
    - Returns current task status
    - Returns extracted panels when task completes
    - Returns error details if task fails
    
    Args:
        task_id: Celery task ID from extraction initiation
        current_user: Current authenticated user
        
    Returns:
        PanelExtractionStatusResponse with task status and panels (if completed)
    """
    try:
        user_id = current_user.get("_id")
        
        result = get_panel_extraction_status(
            task_id=task_id,
            user_id=str(user_id)
        )
        
        return PanelExtractionStatusResponse(
            task_id=result["task_id"],
            status=result["status"],
            image_ids=result.get("image_ids", []),
            extracted_panels_count=result.get("extracted_panels_count", 0),
            extracted_panels=result.get("extracted_panels"),
            message=result.get("message"),
            error=result.get("error")
        )
        
    except ELIESException:
        raise
    except Exception as e:
        logger.error("Failed to get extraction status for task %s: %s", task_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get extraction status"
        )


@router.get(
    "/{image_id}/panels",
    response_model=List[ImageResponse],
    status_code=status.HTTP_200_OK
)
def get_panels_from_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all panels extracted from a specific source image
    
    Returns a list of all panel images that were extracted from the
    specified source image, including their metadata and bounding boxes.
    
    Args:
        image_id: MongoDB ID of the source image
        current_user: Current authenticated user
        
    Returns:
        List of ImageResponse objects for all panels from this source image
        
    Raises:
        HTTP 404: If source image not found
    """
    user_id = str(current_user.get("_id"))
    
    # Verify source image exists and belongs to user
    get_owned_resource(get_images_collection, image_id, user_id, "Image")
    
    # Get all panels from this source image
    panels = get_panels_by_source_image(
        source_image_id=image_id,
        user_id=user_id
    )
    
    # Convert to response format
    response_panels = []
    for panel_doc in panels:
        response_panels.append(ImageResponse(
            _id=str(panel_doc.get("_id")),
            user_id=panel_doc.get("user_id"),
            filename=panel_doc.get("filename"),
            file_path=panel_doc.get("file_path"),
            file_size=panel_doc.get("file_size"),
            source_type=panel_doc.get("source_type"),
            document_id=panel_doc.get("document_id"),
            source_image_id=panel_doc.get("source_image_id"),
            panel_id=panel_doc.get("panel_id"),
            panel_type=panel_doc.get("panel_type"),
            bbox=panel_doc.get("bbox"),
            uploaded_date=panel_doc.get("uploaded_date")
        ))
    
    return response_panels


# ============================================================================
# IMAGE TYPE MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/{image_id}/types", response_model=ImageResponse, status_code=status.HTTP_200_OK)
def add_image_types(
    image_id: str,
    request: ImageTypesUpdateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Add types to an image's image_type list
    
    Adds new types to the image_type list, automatically deduplicating
    and merging with existing types. This is useful for manually tagging
    images with semantic types like 'figure', 'table', etc.
    
    Args:
        image_id: MongoDB ID of the image
        request_body: Request with 'types' list to add
        current_user: Current authenticated user
        
    Returns:
        Updated ImageResponse with new types
        
    Raises:
        HTTP 400: If image_id is invalid
        HTTP 404: If image not found or doesn't belong to user
    """
    user_id = str(current_user.get("_id"))
    images_col = get_images_collection()
    
    # Verify image exists and belongs to user
    image_doc = get_owned_resource(get_images_collection, image_id, user_id, "Image")
    
    # Get existing types and merge with new types (union, no duplicates)
    existing_types = image_doc.get("image_type", [])
    new_types = request.types
    
    # Merge and deduplicate
    merged_types = list(set(existing_types + new_types))
    merged_types.sort()  # Sort for consistency
    
    # Update in database
    images_col.update_one(
        {"_id": ObjectId(image_id)},
        {"$set": {"image_type": merged_types}}
    )
    
    # Update CBIR labels asynchronously (if image has file_path)
    if image_doc.get("file_path"):
        cbir_update_labels.delay(
            user_id=user_id,
            image_id=image_id,
            image_path=image_doc["file_path"],
            labels=merged_types
        )
    
    updated_doc = images_col.find_one({"_id": ObjectId(image_id)})
    return image_response(updated_doc, current_user)


@router.delete("/{image_id}/types/{type_name}", response_model=ImageResponse, status_code=status.HTTP_200_OK)
def remove_image_type(
    image_id: str,
    type_name: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Remove a type from an image's image_type list
    
    Removes a specific type from the image_type list. If the type
    doesn't exist, no error is raised.
    
    Args:
        image_id: MongoDB ID of the image
        type_name: Name of the type to remove
        current_user: Current authenticated user
        
    Returns:
        Updated ImageResponse with type removed
        
    Raises:
        HTTP 400: If image_id is invalid
        HTTP 404: If image not found or doesn't belong to user
    """
    user_id = str(current_user.get("_id"))
    images_col = get_images_collection()
    
    # Verify image exists and belongs to user
    image_doc = get_owned_resource(get_images_collection, image_id, user_id, "Image")
    
    # Get existing types and remove the specified type
    existing_types = image_doc.get("image_type", [])
    updated_types = [t for t in existing_types if t != type_name]
    
    # Update in database
    images_col.update_one(
        {"_id": ObjectId(image_id)},
        {"$set": {"image_type": updated_types}}
    )
    
    # Update CBIR labels asynchronously (if image has file_path)
    if image_doc.get("file_path"):
        cbir_update_labels.delay(
            user_id=user_id,
            image_id=image_id,
            image_path=image_doc["file_path"],
            labels=updated_types
        )
    
    updated_doc = images_col.find_one({"_id": ObjectId(image_id)})
    return image_response(updated_doc, current_user)


@router.get("/types/all", status_code=status.HTTP_200_OK, deprecated=True)
def list_all_image_types(
    current_user: dict = Depends(get_current_user)
):
    """
    Use GET /images/tags instead: it returns the same types as a plain list.

    Get all unique image types used in the system
    
    Returns a list of all unique image types that have been assigned to any
    image in the system. This helps with understanding what types are in use
    and can be used to populate type selection dropdowns.
    
    Args:
        current_user: Current authenticated user
        
    Returns:
        ImageTypeListResponse with list of types and count
    """
    user_id = str(current_user.get("_id"))
    images_col = get_images_collection()
    
    # Aggregate all image_type values for this user
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$unwind": "$image_type"},
        {"$group": {"_id": "$image_type"}},
        {"$sort": {"_id": 1}}
    ]
    
    results = list(images_col.aggregate(pipeline))
    types = [doc["_id"] for doc in results]
    
    return {
        "types": types,
        "count": len(types)
    }


