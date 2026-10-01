"""
Document upload routes for PDF file management
"""
import logging
import math
from pathlib import Path
from typing import List

from celery.result import AsyncResult
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.celery_config import celery_app
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_documents_collection, get_images_collection
from app.exceptions import ResourceNotFoundError, TransientError
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
from app.services.job_logger import create_job_log, ensure_job_capacity, find_job_by_celery_task
from app.services.task_submission import submit_task
from app.services.quota_helpers import augment_list_with_quota, augment_with_quota
from app.services.resource_helpers import get_owned_resource
from app.services.upload_service import save_uploaded_pdf
from app.services.watermark_removal_service import (
    get_watermark_removal_status,
    initiate_watermark_removal,
)
from app.tasks.image_extraction import extract_images_from_document
from app.utils.file_storage import get_extraction_output_path
from app.utils.docker_cbir import check_cbir_health
from app.utils.security import get_current_user, get_current_user_media

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload a PDF document

    - Validates the extension, size, quota and that the content is a PDF
    - Stores the file as pdfs/{_id}.pdf; the client filename is kept only
      as display metadata
    - Queues image extraction

    Raises:
        HTTP 400: Invalid file
        HTTP 413: If storage quota would be exceeded
        HTTP 503: CBIR or the task queue is unavailable
    """
    # Extracted images are indexed in CBIR, so block uploads while it is down
    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        logger.warning("CBIR service unavailable: %s", cbir_message)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to upload documents at this time. Please try again in a few minutes."
        )

    user_id_str = str(current_user["_id"])
    user_quota = current_user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)
    ensure_job_capacity(user_id_str)

    doc_record = save_uploaded_pdf(current_user, file.filename, file.file)
    doc_oid = doc_record["_id"]
    doc_id = str(doc_oid)

    # Create extraction output directory
    get_extraction_output_path(user_id_str, doc_id)

    job_id = create_job_log(
        user_id=user_id_str,
        job_type=JobType.IMAGE_EXTRACTION,
        title=f"Image Extraction: {doc_record['filename']}",
        input_data={"document_id": doc_id, "filename": doc_record["filename"]}
    )

    documents_col = get_documents_collection()
    try:
        task = submit_task(extract_images_from_document, dict(
            doc_id=doc_id,
            user_id=user_id_str,
            pdf_path=doc_record["file_path"],
            job_id=job_id
        ), owner_id=user_id_str, job_id=job_id)
    except TransientError:
        documents_col.update_one(
            {"_id": doc_oid},
            {"$set": {"extraction_status": "failed", "extraction_errors": ["Task queue unavailable"]}}
        )
        raise

    documents_col.update_one({"_id": doc_oid}, {"$set": {"task_id": task.id}})
    doc_record["task_id"] = task.id
    doc_record["_id"] = doc_id
    doc_record = augment_with_quota(doc_record, user_id_str, user_quota)
    return DocumentResponse(**doc_record)


@router.get("", response_model=PaginatedDocumentResponse)
def list_documents(
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
    
    # Convert to response models with quota info (usage read once per page)
    for doc in documents:
        doc["_id"] = str(doc["_id"])
    responses = [DocumentResponse(**doc) for doc in augment_list_with_quota(documents, user_id_str, user_quota)]
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


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
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
    doc = get_owned_resource(
        get_documents_collection,
        doc_id,
        user_id_str,
        "Document"
    )
    
    doc["_id"] = doc_id
    
    # Add quota information
    return DocumentResponse(**augment_with_quota(doc, user_id_str, user_quota))


@router.get("/{doc_id}/images", response_model=List[ImageResponse])
def get_document_images(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0)
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
    get_owned_resource(
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
def download_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user_media)
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
    doc = get_owned_resource(
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
def delete_document(
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
    delete_document_and_artifacts(
        document_id=doc_id,
        user_id=str(current_user["_id"])
    )


# ============================================================================
# TASK STATUS ENDPOINTS
# ============================================================================

@router.get("/tasks/{task_id}", tags=["documents"])
def get_task_status(
    task_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get status of one of the current user's image extraction tasks

    The status can be:
    - PENDING: Task is waiting in the queue
    - STARTED: Task has started processing
    - SUCCESS: Task completed successfully
    - FAILURE: Task failed
    - RETRY: Task is retrying after failure
    - REVOKED: Task was cancelled

    Returns 404 for tasks that belong to other users.
    """
    user_id_str = str(current_user["_id"])
    owned = (
        get_documents_collection().find_one({"task_id": task_id, "user_id": user_id_str}, {"_id": 1})
        or find_job_by_celery_task(task_id, user_id_str)
    )
    if not owned:
        raise ResourceNotFoundError("Task", task_id)

    task = AsyncResult(task_id, app=celery_app)
    response = {"task_id": task_id, "status": task.status}
    if task.successful():
        response["result"] = task.result
    elif task.failed() or task.status == "RETRY":
        response["error"] = str(task.info)
    return response


# ============================================================================
# WATERMARK REMOVAL ENDPOINTS (BEFORE /{doc_id} routes to avoid path conflicts)
# ============================================================================

@router.post(
    "/{doc_id}/remove-watermark",
    response_model=WatermarkRemovalInitiationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["documents"]
)
def initiate_watermark_removal_endpoint(
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
    user_id_str = str(current_user["_id"])
    
    result = initiate_watermark_removal(
        document_id=doc_id,
        user_id=user_id_str,
        aggressiveness_mode=request.aggressiveness_mode
    )
    
    return result


@router.get(
    "/{doc_id}/watermark-removal/status",
    response_model=WatermarkRemovalStatusResponse,
    tags=["documents"]
)
def get_watermark_removal_status_endpoint(
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
    user_id_str = str(current_user["_id"])
    
    status_info = get_watermark_removal_status(
        document_id=doc_id,
        user_id=user_id_str
    )
    
    return status_info


