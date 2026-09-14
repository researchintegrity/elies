"""
Document upload routes for PDF file management
"""
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from bson import ObjectId
from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.celery_config import celery_app
from app.config.settings import convert_host_path_to_container
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_documents_collection, get_images_collection
from app.schemas import (
    DocumentResponse,
    ImageResponse,
    PaginatedDocumentResponse,
    WatermarkRemovalInitiationResponse,
    WatermarkRemovalRequest,
    WatermarkRemovalStatusResponse,
    JobType,
)
from app.services.document_service import delete_document_and_artifacts
from app.services.job_logger import create_job_log
from app.services.quota_helpers import augment_with_quota
from app.services.resource_helpers import get_owned_resource
from app.services.watermark_removal_service import (
    get_watermark_removal_status,
    initiate_watermark_removal,
)
from app.tasks.image_extraction import extract_images_from_document
from app.utils.file_storage import (
    check_storage_quota,
    get_extraction_output_path,
    save_pdf_file,
    update_user_storage_in_db,
    validate_pdf,
)
from app.utils.docker_cbir import check_cbir_health
from app.utils.security import get_current_user

logger = logging.getLogger(__name__)
_warned_deprecated = False

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload a PDF document
    
    - Validates PDF file
    - Checks storage quota before saving
    - Saves to disk organized by user
    - Creates document record in MongoDB
    - Sets up extraction folder
    - Triggers figure extraction placeholder
    
    Args:
        file: PDF file to upload
        current_user: Current authenticated user
        
    Returns:
        DocumentResponse with document info
        
    Raises:
        HTTP 413: If storage quota would be exceeded
    """
    try:
        # Pre-flight CBIR health check - block upload if CBIR is unavailable
        # (extracted images won't be indexable)
        cbir_healthy, cbir_message = check_cbir_health()
        if not cbir_healthy:
            logger.warning(f"CBIR service unavailable: {cbir_message}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Unable to upload documents at this time. Please try again in a few minutes."
            )
        
        # Read file content
        content = await file.read()
        file_size = len(content)
        
        # Validate PDF
        is_valid, error_msg = validate_pdf(file.filename, file_size)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        
        # Convert user_id to string
        user_id_str = str(current_user["_id"])
        
        # Check storage quota BEFORE saving file
        user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
        quota_ok, quota_error = check_storage_quota(user_id_str, file_size, user_quota)
        if not quota_ok:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=quota_error
            )
        
        # Save PDF file
        try:
            file_path, saved_size = save_pdf_file(
                user_id_str,
                content,
                file.filename
            )
        except IOError as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save file: {str(e)}"
            )
        
        # Create document record in MongoDB
        documents_col = get_documents_collection()
        
        doc_data = {
            "user_id": user_id_str,
            "filename": file.filename,
            "file_path": file_path,
            "file_size": saved_size,
            "extraction_status": "pending",
            "extracted_image_count": 0,
            "extraction_errors": [],
            "uploaded_date": datetime.utcnow()
        }
        
        result = documents_col.insert_one(doc_data)
        doc_id = str(result.inserted_id)
        
        # Rename file to use MongoDB _id
        new_filename = Path(f"{doc_id}.pdf")
        
        # Construct full paths using pathlib
        old_path = Path(file_path)
        if not old_path.is_absolute():
            old_path = Path.cwd() / old_path
        
        new_full_path = old_path.parent / new_filename
        
        try:
            old_path.rename(new_full_path)
        except OSError as e:
            # Delete MongoDB doc since we can't rename the file
            documents_col.delete_one({"_id": ObjectId(doc_id)})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to rename uploaded file: {str(e)}"
            )
        
        # Update MongoDB with new filename with container-compatible path
        file_path = Path(file_path).parent / new_filename
        storage_path = convert_host_path_to_container(file_path)
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "file_path": str(storage_path)
                }
            }
        )
        
        # Create extraction output directory
        get_extraction_output_path(user_id_str, doc_id)
        
        # Create job log entry for the jobs dashboard (pending state)
        job_id = create_job_log(
            user_id=user_id_str,
            job_type=JobType.IMAGE_EXTRACTION,
            title=f"Image Extraction: {new_filename}",
            input_data={"document_id": doc_id, "filename": str(new_filename)}
        )
        
        # ✨ QUEUE IMAGE EXTRACTION TASK (asynchronous - returns immediately)
        task = extract_images_from_document.delay(
            doc_id=doc_id,
            user_id=user_id_str,
            pdf_path=str(storage_path),
            job_id=job_id
        )
        
        # Store task_id in document for status checking
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"task_id": task.id}}
        )
        
        # Retrieve and return updated document with quota info
        doc_record = documents_col.find_one({"_id": ObjectId(doc_id)})
        doc_record["_id"] = doc_id  # Ensure _id is set for response



        
        # Add quota information to response
        doc_record = augment_with_quota(doc_record, user_id_str, user_quota)
        
        # Update user storage in database for easy access
        update_user_storage_in_db(user_id_str)
        
        return DocumentResponse(**doc_record)
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {str(e)}"
        )


@router.get("", response_model=PaginatedDocumentResponse)
async def list_documents(
    current_user: dict = Depends(get_current_user),
    page: int = Query(1, ge=1),
    per_page: int = Query(12, ge=1, le=24)
):
    """
    List all documents uploaded by current user with pagination.
    
    Args:
        current_user: Current authenticated user
        page: Page number (1-indexed, minimum 1). default: 1
        per_page: Number of items per page (default: 12, max: 24)
        
    Returns:
        PaginatedDocumentResponse
    """
    documents_col = get_documents_collection()
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    # Pagination
    actual_offset = (page - 1) * per_page
    actual_limit = per_page
    
    # Build query
    query = {"user_id": user_id_str}
    
    # Query documents for user
    documents = list(
        documents_col.find(query)
        .sort("uploaded_date", -1)
        .skip(actual_offset)
        .limit(actual_limit)
    )
    
    # Convert to response models with quota info
    responses = []
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        doc = augment_with_quota(doc, user_id_str, user_quota)
        responses.append(DocumentResponse(**doc))
    # Get total count for pagination
    total = documents_col.count_documents(query)

    # Return paginated response with metadata
    total_pages = math.ceil(total / per_page) if total > 0 else 1
    
    return PaginatedDocumentResponse(
        items=responses,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


@router.get("/{doc_id}")
async def get_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific document by ID
    
    Args:
        doc_id: Document ID
        current_user: Current authenticated user
        
    Returns:
        DocumentResponse with storage quota info
    """
    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    
    # Get document with ownership validation
    doc = await get_owned_resource(
        get_documents_collection,
        doc_id,
        user_id_str,
        "Document"
    )
    
    doc["_id"] = doc_id
    
    # Add quota information
    doc = augment_with_quota(doc, user_id_str, user_quota)
    
    # Return raw dict (convert ObjectId and datetime for JSON serialization)
    result = {}
    for key, value in doc.items():
        if isinstance(value, ObjectId):
            result[key] = str(value)
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    
    return result


@router.get("/{doc_id}/images", response_model=List[ImageResponse])
async def get_document_images(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
    limit: int = 50,
    offset: int = 0
):
    """
    Get all extracted images from a specific document
    
    Args:
        doc_id: Document ID
        current_user: Current authenticated user
        limit: Maximum number of images to return
        offset: Number of images to skip
        
    Returns:
        List of ImageResponse objects (extracted images only)
    """
    user_id_str = str(current_user["_id"])
    
    # Verify document belongs to user
    await get_owned_resource(
        get_documents_collection,
        doc_id,
        user_id_str,
        "Document"
    )
    
    # Get images for this document
    images_col = get_images_collection()
    images = list(
        images_col.find({
            "document_id": doc_id,
            "user_id": user_id_str,
            "source_type": "extracted"
        })
        .sort("uploaded_date", -1)
        .skip(offset)
        .limit(limit)
    )
    
    # Convert to response models
    responses = []
    for img in images:
        img["_id"] = str(img["_id"])
        responses.append(ImageResponse(**img))
    
    return responses


@router.get("/{doc_id}/download")
async def download_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Download a document (PDF file)
    
    Args:
        doc_id: Document ID
        current_user: Current authenticated user
        
    Returns:
        FileResponse with PDF file
    """
    user_id_str = str(current_user["_id"])
    
    # Verify document belongs to user
    doc = await get_owned_resource(
        get_documents_collection,
        doc_id,
        user_id_str,
        "Document"
    )
    
    # Check if file exists
    file_path = doc["file_path"]
    if not Path(file_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on disk"
        )
    
    # Return file
    return FileResponse(
        path=file_path,
        filename=doc["filename"],
        media_type="application/pdf"
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
) -> None:
    """
    Delete a document and its associated extracted images and annotations.
    
    The document file, extracted images, and all annotations are removed.
    
    Args:
        doc_id: Document ID to delete.
        current_user: Current authenticated user.
        
    Raises:
        ValidationError: If document ID format is invalid.
        ResourceNotFoundError: If document not found.
        FileOperationError: If file deletion fails.
    """
    await delete_document_and_artifacts(
        document_id=doc_id,
        user_id=str(current_user["_id"])
    )


# ============================================================================
# TASK STATUS ENDPOINTS
# ============================================================================

@router.get("/tasks/{task_id}", tags=["documents"])
async def get_task_status(
    task_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get status of image extraction task
    
    Query a background task's status. The status can be:
    - PENDING: Task is waiting in the queue
    - STARTED: Task has started processing
    - SUCCESS: Task completed successfully
    - FAILURE: Task failed
    - RETRY: Task is retrying after failure
    - REVOKED: Task was cancelled
    
    Returns:
        {
            "task_id": "abc-123-def",
            "status": "SUCCESS",
            "result": {
                "doc_id": "507f1f77bcf86cd799439011",
                "extracted_count": 5,
                "errors": []
            }
        }
    """
    try:
        task = AsyncResult(task_id, app=celery_app)
        
        response = {
            "task_id": task_id,
            "status": task.status,
        }
        
        if task.successful():
            response["result"] = task.result
        elif task.failed():
            response["error"] = str(task.info)
        elif task.status == "RETRY":
            response["error"] = str(task.info)
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve task status: {str(e)}"
        )


# ============================================================================
# WATERMARK REMOVAL ENDPOINTS (BEFORE /{doc_id} routes to avoid path conflicts)
# ============================================================================

@router.post(
    "/{doc_id}/remove-watermark",
    response_model=WatermarkRemovalInitiationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"]
)
async def initiate_watermark_removal_endpoint(
    doc_id: str,
    request: WatermarkRemovalRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Initiate watermark removal for a PDF document
    
    This endpoint queues an async task to remove watermarks from a PDF.
    The original PDF is preserved, and a new cleaned version is created.
    
    Query the status using: GET /documents/{doc_id}/watermark-removal/status
    
    Args:
        doc_id: Document ID to remove watermark from
        request: WatermarkRemovalRequest with aggressiveness_mode (1, 2, or 3)
        current_user: Current authenticated user
        
    Returns:
        WatermarkRemovalInitiationResponse with task info
        
    Raises:
        HTTP 400: Invalid aggressiveness mode or document is not a PDF
        HTTP 404: Document not found
        HTTP 500: Server error
    """
    try:
        user_id_str = str(current_user["_id"])
        
        result = await initiate_watermark_removal(
            document_id=doc_id,
            user_id=user_id_str,
            aggressiveness_mode=request.aggressiveness_mode
        )
        
        return result
    
    except ValueError as e:
        error_msg = str(e)
        if "Invalid aggressiveness mode" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        elif "Invalid document ID" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        elif "Document not found" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_msg
            )
        elif "not a PDF" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
    except Exception as e:
        logger.error(f"Error initiating watermark removal: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate watermark removal: {str(e)}"
        )


@router.get(
    "/{doc_id}/watermark-removal/status",
    response_model=WatermarkRemovalStatusResponse,
    tags=["documents"]
)
async def get_watermark_removal_status_endpoint(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get watermark removal status for a document
    
    Query the status of an ongoing or completed watermark removal task.
    
    Status values:
    - not_started: Watermark removal has not been initiated
    - queued: Task is queued in the task queue
    - processing: Watermark removal is in progress
    - completed: Watermark removal completed successfully
    - failed: Watermark removal failed
    
    When status is "completed", the response includes:
    - output_filename: Name of the cleaned PDF
    - output_size: Size of cleaned PDF in bytes
    - cleaned_document_id: Document ID of the cleaned PDF for download
    
    Args:
        doc_id: Document ID to check status for
        current_user: Current authenticated user
        
    Returns:
        WatermarkRemovalStatusResponse with current status
        
    Raises:
        HTTP 404: Document not found
        HTTP 500: Server error
    """
    try:
        user_id_str = str(current_user["_id"])
        
        status_info = await get_watermark_removal_status(
            document_id=doc_id,
            user_id=user_id_str
        )
        
        return status_info
    
    except ValueError as e:
        error_msg = str(e)
        if "Invalid document ID" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
        elif "Document not found" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_msg
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg
            )
    except Exception as e:
        logger.error(f"Error retrieving watermark removal status: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve watermark removal status: {str(e)}"
        )
