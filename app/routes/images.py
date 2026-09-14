"""
Image upload routes for extracted and user-uploaded image management
"""
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from PIL import Image

from app.celery_config import celery_app
from app.config.settings import (
    DEFAULT_THUMBNAIL_SIZE,
    THUMBNAIL_JPEG_QUALITY,
    convert_host_path_to_container,
)
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import (
    get_documents_collection,
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
from app.services.image_service import (
    delete_image_and_artifacts,
    list_images as list_images_service,
)
from app.services.job_logger import create_job_log, complete_job
from app.services.panel_extraction_service import (
    get_panel_extraction_status,
    get_panels_by_source_image,
    initiate_panel_extraction,
)
from app.services.quota_helpers import augment_with_quota, augment_list_with_quota
from app.services.resource_helpers import get_owned_resource
from app.tasks.cbir import cbir_index_image, cbir_update_labels, cbir_index_batch_with_progress
from app.utils.docker_cbir import check_cbir_health
from app.utils.file_storage import (
    check_storage_quota,
    get_thumbnail_path,
    save_image_file,
    update_user_storage_in_db,
    validate_image,
)
from app.utils.metadata_parser import extract_exif_metadata
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["images"])


@router.post("/upload", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile = File(...),
    document_id: str = Query(None, description="Optional document ID if image is related to a document"),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload an image file (for user-uploaded images)
    
    - Validates image file
    - Checks storage quota before saving
    - Saves to disk in user's images/uploaded/ directory
    - Renames file to {_id}.{ext} after MongoDB insertion
    - Creates image record in MongoDB with new fields
    - Optionally links to a document
    
    Args:
        file: Image file to upload
        document_id: Optional document ID to link image to
        current_user: Current authenticated user
        
    Returns:
        ImageResponse with image info
        
    Raises:
        HTTP 413: If storage quota would be exceeded
    """
    try:
        import os
        from app.config.settings import convert_container_path_to_host
        
        # Pre-flight CBIR health check - block upload if CBIR is unavailable
        cbir_healthy, cbir_message = check_cbir_health()
        if not cbir_healthy:
            logger.warning(f"CBIR service unavailable: {cbir_message}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to upload images at this time. Please try again in a few minutes."
            )
        
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Validate image
        is_valid, error_msg = validate_image(file.filename, file_size)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        
        user_id_str = str(current_user["_id"])
        
        # Check storage quota BEFORE saving file
        user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
        quota_ok, quota_error = check_storage_quota(user_id_str, file_size, user_quota)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=quota_error
            )
        
        # Save image file
        try:
            file_path, saved_size = save_image_file(
                user_id_str,
                content,
                file.filename,
                doc_id=None  # User-uploaded, not extracted
            )
        except IOError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save file: {str(e)}"
            )
        
        # If document_id provided, verify it belongs to user
        if document_id:
            documents_col = get_documents_collection()
            try:
                doc = documents_col.find_one({
                    "_id": ObjectId(document_id),
                    "user_id": user_id_str
                })
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid document ID"
                )
            
            if not doc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )
        
        # Extract EXIF metadata
        exif_metadata = extract_exif_metadata(file_path)

        # Create image record in MongoDB
        images_col = get_images_collection()
        
        img_data = {
            "user_id": user_id_str,
            "filename": file.filename,
            "file_path": file_path,
            "file_size": saved_size,
            "source_type": "uploaded",
            "document_id": document_id,  # Can be None for user-uploaded
            "pdf_page": None,  # Not applicable for uploaded images
            "page_bbox": None,
            "extraction_mode": None,
            "original_filename": file.filename,  # Store original name
            "image_type": [],  # Empty for user-uploaded, can be edited later
            "uploaded_date": datetime.utcnow(),
            "exif_metadata": exif_metadata
        }
        
        result = images_col.insert_one(img_data)
        image_id = result.inserted_id
        
        # Rename file to use MongoDB _id
        file_ext = Path(file.filename).suffix
        new_filename = Path(file.filename).with_name(f"{image_id}{file_ext}")
        
        # Construct full paths using pathlib
        old_path = Path(file_path)
        if not old_path.is_absolute():
            old_path = Path.cwd() / old_path
        
        new_full_path = old_path.parent / new_filename
        
        try:
            old_path.rename(new_full_path)
        except OSError as e:
            # Delete MongoDB doc since we can't rename the file
            images_col.delete_one({"_id": image_id})
            raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rename uploaded file: {str(e)}"
            )
        
        # Update MongoDB with new filename with container-compatible path
        # ISSUE IS HERE
        file_path = Path(file_path).parent / new_filename
        storage_path = convert_host_path_to_container(file_path)
        images_col.update_one(
            {"_id": image_id},
            {
                "$set": {
                    "filename": str(new_filename),
                    "file_path": str(storage_path)
                }
            }
        )
        
        # Retrieve and return created image with quota info
        img_record = images_col.find_one({"_id": image_id})
        img_record["_id"] = str(image_id)
        
        # Add quota information to response
        img_record = augment_with_quota(img_record, user_id_str, user_quota)
        
        # Update user storage in database for easy access
        update_user_storage_in_db(user_id_str)
        
        # Trigger CBIR indexing asynchronously
        try:
            cbir_index_image.delay(
                user_id=user_id_str,
                image_id=str(image_id),
                image_path=str(storage_path),
                labels=img_record.get("image_type", [])
            )
        except Exception as e:
            # Log but don't fail the upload if CBIR indexing fails to queue
            import logging
            logging.getLogger(__name__).warning(f"Failed to queue CBIR indexing for image {image_id}: {e}")
        
        return ImageResponse(**img_record)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}"
        )


# ============================================================================
# BATCH UPLOAD AND INDEXING STATUS ENDPOINTS
# ============================================================================

@router.post("/upload/batch", response_model=BatchUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_images_batch(
    files: List[UploadFile] = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload multiple images in a single request with progress tracking.
    
    - Validates and saves all images
    - Creates MongoDB records for each
    - Starts a single Celery task to index all images with progress tracking
    - Returns a job_id to poll for indexing progress
    
    Args:
        files: List of image files to upload
        current_user: Current authenticated user
        
    Returns:
        BatchUploadResponse with job_id for progress tracking
        
    Raises:
        HTTP 400: If no valid images provided
        HTTP 413: If storage quota would be exceeded
    """
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    # Pre-flight CBIR health check - block upload if CBIR is unavailable
    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        logger.warning(f"CBIR service unavailable: {cbir_message}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to upload images at this time. Please try again in a few minutes."
        )
    
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one image file is required"
        )
    
    images_col = get_images_collection()
    uploaded_images = []
    total_uploaded_size = 0
    
    # Process files one at a time to avoid memory issues
    for file in files:
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Early validation before quota check
        is_valid, error_msg = validate_image(file.filename, file_size)
        if not is_valid:
            logger.warning(f"Skipping invalid image {file.filename}: {error_msg}")
            continue
        
        # Incremental quota check for this file
        quota_ok, quota_error = check_storage_quota(
            user_id_str, 
            file_size + total_uploaded_size, 
            user_quota
        )
        if not quota_ok:
            logger.warning(f"Skipping {file.filename}: {quota_error}")
            # If we have already uploaded some files, continue with what we have
            # Otherwise, this would fail the entire batch
            if not uploaded_images:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=quota_error
                )
            break  # Stop processing more files, proceed with what we have
        
        try:
            # Save image file
            file_path, saved_size = save_image_file(
                user_id_str,
                content,
                file.filename,
                doc_id=None
            )
            
            # Track uploaded size for incremental quota
            total_uploaded_size += saved_size
            
            # Extract EXIF metadata
            exif_metadata = extract_exif_metadata(file_path)
            
            # Create image record
            img_data = {
                "user_id": user_id_str,
                "filename": file.filename,
                "file_path": file_path,
                "file_size": saved_size,
                "source_type": "uploaded",
                "document_id": None,
                "pdf_page": None,
                "page_bbox": None,
                "extraction_mode": None,
                "original_filename": file.filename,
                "image_type": [],
                "uploaded_date": datetime.utcnow(),
                "exif_metadata": exif_metadata
            }
            
            result = images_col.insert_one(img_data)
            image_id = result.inserted_id
            
            # Rename file to use MongoDB _id
            file_ext = Path(file.filename).suffix
            new_filename = Path(file.filename).with_name(f"{image_id}{file_ext}")
            
            old_path = Path(file_path)
            if not old_path.is_absolute():
                old_path = Path.cwd() / old_path
            new_full_path = old_path.parent / new_filename
            try:
                old_path.rename(new_full_path)
                final_path = new_full_path
                final_filename = new_filename
            except OSError as rename_err:
                # If rename fails, keep the original path/filename so DB stays consistent
                logger.error(
                    "Failed to rename image file from '%s' to '%s': %s",
                    old_path,
                    new_full_path,
                    rename_err,
                )
                final_path = old_path
                final_filename = Path(file_path).name
            # Update MongoDB with the actual path
            storage_path = convert_host_path_to_container(final_path)
            images_col.update_one(
                {"_id": image_id},
                {"$set": {"filename": str(final_filename), "file_path": str(storage_path)}}
            )
            
            uploaded_images.append({
                "image_id": str(image_id),
                "image_path": str(storage_path),
                "labels": []
            })
            
        except Exception as e:
            logger.error(f"Failed to save image {file.filename}: {e}")
            continue
    
    if not uploaded_images:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid images could be uploaded"
        )
    
    # Create job log entry for the jobs dashboard (tracks the batch upload)
    main_job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.BATCH_UPLOAD,
        title=f"Batch Upload ({len(uploaded_images)} images)",
        input_data={"image_count": len(uploaded_images), "image_ids": [img["image_id"] for img in uploaded_images]}
    )
    
    # Update user storage
    try:
        update_user_storage_in_db(user_id_str)
    except Exception as e:
        logger.error(f"Failed to update user storage for user {user_id_str}: {e}")
        complete_job(main_job_id, user_id_str, JobStatus.FAILED, errors=[f"Failed to update storage: {str(e)}"])
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user storage after upload"
        )
    
    # Create indexing job in MongoDB
    job_id = f"idx_{user_id_str}_{int(time.time())}"
    jobs_col = get_indexing_jobs_collection()
    
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
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
        "completed_at": None
    }

    try:

        jobs_col.insert_one(job_doc)

    except Exception as e:
        logger.error(f"Failed to create indexing job document: {e}")
        # Cleanup uploaded images to avoid orphaned images when job creation fails
        for img in uploaded_images:
            image_id = img.get("image_id")
            if not image_id:
                continue
            try:
                delete_image_and_artifacts(image_id=image_id, user_id=user_id_str)
            except Exception as cleanup_err:
                logger.error(
                    f"Failed to clean up image {image_id} after job creation failure: {cleanup_err}"
                )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create indexing job for uploaded images. Upload has been rolled back.",
        )
    
    # Start Celery task for batch indexing with progress
    try:
        cbir_index_batch_with_progress.delay(
            job_id=job_id,
            user_id=user_id_str,
            image_items=uploaded_images,
            main_job_id=main_job_id
        )
    except Exception as e:
        logger.error(f"Failed to queue batch indexing task: {e}")
        complete_job(main_job_id, user_id_str, JobStatus.FAILED, errors=[f"Failed to queue indexing: {str(e)}"])
        # Update job status to failed
        # Attempt to roll back uploaded images to avoid orphaned resources
        cleanup_errors = []
        for item in uploaded_images:
            image_id = item.get("image_id")
            if not image_id:
                continue
            try:
                # delete_image_and_artifacts is expected to remove both DB records and files
                delete_image_and_artifacts(image_id)
            except Exception as cleanup_exc:
                err_msg = f"Failed to clean up image {image_id} after indexing queue error: {cleanup_exc}"
                logger.error(err_msg)
                cleanup_errors.append(err_msg)
        # Update job status to failed and record errors
        error_messages = [f"Queueing error: {str(e)}"] + cleanup_errors
        jobs_col.update_one(
            {"_id": job_id},
            {
                "$set": {
                    "status": IndexingJobStatus.FAILED.value,
                    "current_step": "Failed to queue indexing task",
                    "errors": error_messages,
                    "updated_at": datetime.utcnow(),
                    "completed_at": datetime.utcnow(),
                }
            }
        )
    
    return BatchUploadResponse(
        job_id=job_id,
        uploaded_count=len(uploaded_images),
        image_ids=[img["image_id"] for img in uploaded_images],
        message=f"{len(uploaded_images)} images uploaded, indexing in progress"
    )


@router.get("/indexing-status/{job_id}", response_model=IndexingJobResponse)
async def get_indexing_status(
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
async def list_images(
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
    
    try:
        # Pagination
        actual_offset = (page - 1) * per_page
        actual_limit = per_page
        
        # Parse image_type from comma-separated string
        parsed_image_type = [t.strip() for t in image_type.split(",")] if image_type else None
        
        # Use service to get images with all filter parameters
        result = await list_images_service(
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
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/tags", response_model=List[str])
async def get_all_tags(
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
async def get_all_image_ids(
    image_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
    source_type: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all image IDs for the current user in a single request.
    
    This is optimized for "Select All" operations where only IDs are needed.
    Returns a lightweight response with just IDs instead of full image objects.
    
    Supports the same filters as the main /images endpoint.
    
    Returns:
        {"ids": ["id1", "id2", ...], "count": N}
    """
    user_id_str = str(current_user["_id"])
    images_col = get_images_collection()
    
    # Build query with same logic as list_images
    query = {"user_id": user_id_str}
    
    # Apply filters
    if image_type:
        tags = [t.strip() for t in image_type.split(",") if t.strip()]
        if tags:
            query["image_type"] = {"$in": tags}
    
    if source_type and source_type != "all":
        query["source_type"] = source_type
    
    if date_from:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(date_from.replace('Z', '+00:00'))
            query.setdefault("created_at", {})["$gte"] = dt
        except ValueError:
            pass
    
    if date_to:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(date_to.replace('Z', '+00:00'))
            query.setdefault("created_at", {})["$lte"] = dt
        except ValueError:
            pass
    
    if search:
        search_regex = {"$regex": search, "$options": "i"}
        query["$or"] = [
            {"filename": search_regex},
            {"original_filename": search_regex}
        ]
    
    # Only fetch _id field for efficiency
    cursor = images_col.find(query, {"_id": 1})
    ids = [str(doc["_id"]) for doc in cursor]
    
    return {"ids": ids, "count": len(ids)}

@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(
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
    
    img = await get_owned_resource(
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
async def download_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
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
    img = await get_owned_resource(
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
            detail=f"File not found on disk"
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
async def get_image_thumbnail(
    image_id: str,
    current_user: dict = Depends(get_current_user)
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
    img = await get_owned_resource(
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
                detail=f"Original file not found"
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
                
        except Exception as e:
            # If thumbnail generation fails, fallback to original (pass-through)
            logger.warning("Thumbnail generation failed for image %s: %s", image_id, e)
            # Fallback to download_image logic
            return await download_image(image_id, current_user)

    # Return thumbnail
    return FileResponse(
        path=thumb_path,
        filename=f"thumb_{img['filename']}",
        media_type="image/jpeg"
    )


@router.patch("/{image_id}/flag", response_model=ImageResponse)
async def toggle_image_flag(
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
async def delete_image(
    image_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Delete an image file (user-uploaded only)
    
    Args:
        image_id: Image ID
        current_user: Current authenticated user
    """
    await delete_image_and_artifacts(
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
async def initiate_panel_extraction_endpoint(
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
    try:
        user_id = current_user.get("_id")
        
        # Pre-flight CBIR health check - block extraction if CBIR is unavailable
        cbir_healthy, cbir_message = check_cbir_health()
        if not cbir_healthy:
            logger.warning(f"CBIR service unavailable: {cbir_message}")
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
        
    except ValueError as e:
        error_msg = str(e)
        if "not found" in error_msg or "does not belong" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_msg
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
    except HTTPException:
        # Re-raise HTTP exceptions (including 503 from CBIR check)
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate panel extraction: {str(e)}"
        )


@router.get(
    "/extract-panels/status/{task_id}",
    response_model=PanelExtractionStatusResponse,
    status_code=status.HTTP_200_OK
)
async def get_panel_extraction_status_endpoint(
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
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get extraction status: {str(e)}"
        )


@router.get(
    "/{image_id}/panels",
    response_model=List[ImageResponse],
    status_code=status.HTTP_200_OK
)
async def get_panels_from_image(
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
    try:
        user_id = str(current_user.get("_id"))
        
        # Verify source image exists and belongs to user
        images_col = get_images_collection()
        source_image = images_col.find_one(
            {"_id": ObjectId(image_id), "user_id": user_id}
        )
        
        if not source_image:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Source image not found: {image_id}"
            )
        
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
        
    except Exception as e:
        # Check if it's an invalid ObjectId
        if "invalid ObjectId" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid image ID: {image_id}"
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve panels: {str(e)}"
        )


# ============================================================================
# IMAGE TYPE MANAGEMENT ENDPOINTS
# ============================================================================

@router.post("/{image_id}/types", response_model=ImageResponse, status_code=status.HTTP_200_OK)
async def add_image_types(
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
    try:
        user_id = str(current_user.get("_id"))
        images_col = get_images_collection()
        
        # Verify image exists and belongs to user
        image_doc = images_col.find_one(
            {"_id": ObjectId(image_id), "user_id": user_id}
        )
        
        if not image_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Image not found: {image_id}"
            )
        
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
        
        # Fetch updated document
        updated_doc = images_col.find_one({"_id": ObjectId(image_id)})
        
        # Convert to response
        response = ImageResponse(
            _id=str(updated_doc.get("_id")),
            user_id=updated_doc.get("user_id"),
            filename=updated_doc.get("filename"),
            file_path=updated_doc.get("file_path"),
            file_size=updated_doc.get("file_size"),
            source_type=updated_doc.get("source_type"),
            document_id=updated_doc.get("document_id"),
            source_image_id=updated_doc.get("source_image_id"),
            panel_id=updated_doc.get("panel_id"),
            panel_type=updated_doc.get("panel_type"),
            bbox=updated_doc.get("bbox"),
            pdf_page=updated_doc.get("pdf_page"),
            page_bbox=updated_doc.get("page_bbox"),
            extraction_mode=updated_doc.get("extraction_mode"),
            original_filename=updated_doc.get("original_filename"),
            image_type=updated_doc.get("image_type", []),
            uploaded_date=updated_doc.get("uploaded_date"),
            user_storage_used=updated_doc.get("user_storage_used", 0),
            user_storage_remaining=updated_doc.get("user_storage_remaining", DEFAULT_USER_STORAGE_QUOTA)
        )
        
        return response
        
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image ID: {image_id}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add types: {str(e)}"
        )


@router.delete("/{image_id}/types/{type_name}", response_model=ImageResponse, status_code=status.HTTP_200_OK)
async def remove_image_type(
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
    try:
        user_id = str(current_user.get("_id"))
        images_col = get_images_collection()
        
        # Verify image exists and belongs to user
        image_doc = images_col.find_one(
            {"_id": ObjectId(image_id), "user_id": user_id}
        )
        
        if not image_doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Image not found: {image_id}"
            )
        
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
        
        # Fetch updated document
        updated_doc = images_col.find_one({"_id": ObjectId(image_id)})
        
        # Convert to response
        response = ImageResponse(
            _id=str(updated_doc.get("_id")),
            user_id=updated_doc.get("user_id"),
            filename=updated_doc.get("filename"),
            file_path=updated_doc.get("file_path"),
            file_size=updated_doc.get("file_size"),
            source_type=updated_doc.get("source_type"),
            document_id=updated_doc.get("document_id"),
            source_image_id=updated_doc.get("source_image_id"),
            panel_id=updated_doc.get("panel_id"),
            panel_type=updated_doc.get("panel_type"),
            bbox=updated_doc.get("bbox"),
            pdf_page=updated_doc.get("pdf_page"),
            page_bbox=updated_doc.get("page_bbox"),
            extraction_mode=updated_doc.get("extraction_mode"),
            original_filename=updated_doc.get("original_filename"),
            image_type=updated_doc.get("image_type", []),
            uploaded_date=updated_doc.get("uploaded_date"),
            user_storage_used=updated_doc.get("user_storage_used", 0),
            user_storage_remaining=updated_doc.get("user_storage_remaining", DEFAULT_USER_STORAGE_QUOTA)
        )
        
        return response
        
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image ID: {image_id}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove type: {str(e)}"
        )


@router.get("/types/all", status_code=status.HTTP_200_OK)
async def list_all_image_types(
    current_user: dict = Depends(get_current_user)
):
    """
    Get all unique image types used in the system
    
    Returns a list of all unique image types that have been assigned to any
    image in the system. This helps with understanding what types are in use
    and can be used to populate type selection dropdowns.
    
    Args:
        current_user: Current authenticated user
        
    Returns:
        ImageTypeListResponse with list of types and count
    """
    try:
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
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve image types: {str(e)}"
        )
