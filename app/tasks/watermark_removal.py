"""
Watermark removal tasks for async processing
"""
from celery import current_task
from celery.exceptions import SoftTimeLimitExceeded
from app.celery_config import celery_app
from app.db.mongodb import get_documents_collection, get_images_collection
from app.utils.docker_watermark import remove_watermark_with_docker
from app.config.settings import CELERY_MAX_RETRIES, CELERY_RETRY_BACKOFF_BASE, convert_host_path_to_container
from app.schemas import JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from bson import ObjectId
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.remove_watermark")
def remove_watermark_from_document(
    self,
    doc_id: str,
    user_id: str,
    pdf_path: str,
    aggressiveness_mode: int = 1,
    job_id: str = None
):
    """
    Remove watermark from PDF document asynchronously
    
    This task:
    1. Updates document status to 'watermark_removal_processing'
    2. Calls Docker watermark removal
    3. Updates MongoDB with results
    4. Stores cleaned PDF as a new document record
    5. Retries on failure (up to 3 times)
    
    Args:
        doc_id: MongoDB document ID of original PDF
        user_id: User who uploaded document
        pdf_path: Full path to PDF file
        aggressiveness_mode: Watermark removal mode (1, 2, or 3)
        job_id: Optional pre-created job ID from the service (for pending state tracking)
        
    Returns:
        Dict with watermark removal results
        
    Raises:
        Retries automatically with exponential backoff on failure
    """
    documents_col = get_documents_collection()
    
    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.WATERMARK_REMOVAL,
                title=f"Watermark Removal (mode {aggressiveness_mode})",
                celery_task_id=self.request.id,
                input_data={"doc_id": doc_id, "mode": aggressiveness_mode}
            )
        
        logger.info(
            f"Starting watermark removal for doc_id={doc_id}, mode={aggressiveness_mode}"
        )
        
        # Update status to processing
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Starting watermark removal...")
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "watermark_removal_status": "processing",
                    "watermark_removal_started_at": datetime.utcnow(),
                    "watermark_removal_retry_count": self.request.retries,
                    "watermark_removal_mode": aggressiveness_mode
                }
            }
        )
        
        update_job_progress(job_id, user_id, None, 30, "Running Docker watermark removal...")
        
        # Run watermark removal using Docker
        success, status_message, output_file_info = remove_watermark_with_docker(
            doc_id=doc_id,
            user_id=user_id,
            pdf_file_path=pdf_path,
            aggressiveness_mode=aggressiveness_mode
        )
        
        # Determine final status and prepare update
        if success:
            watermark_status = "completed"
            
            # Get file size of cleaned PDF
            output_file_path = str(convert_host_path_to_container(output_file_info.get("path")))
            output_file_size = output_file_info.get("size", 0)
            output_filename = output_file_info.get("filename")
            
            logger.info(
                f"Watermark removal successful for doc_id={doc_id}: "
                f"output_file={output_filename}, size={output_file_size}"
            )
            
            update_job_progress(job_id, user_id, None, 80, "Creating cleaned document record...")
            
            # Create a new document record for the cleaned PDF
            # (keeping original document intact)
            cleaned_doc_data = {
                "user_id": user_id,
                "filename": output_filename,
                "file_path": output_file_path,
                "file_size": output_file_size,
                "original_document_id": doc_id,
                "watermark_removal_mode": aggressiveness_mode,
                "extraction_status": "images not extracted - watermark removed",
                "extracted_image_count": 0,
                "extraction_errors": [],
                "uploaded_date": datetime.utcnow(),
                "is_watermark_removed": True
            }
            
            result = documents_col.insert_one(cleaned_doc_data)
            cleaned_doc_id = str(result.inserted_id)
            
            logger.info(f"Created new document record for cleaned PDF: {cleaned_doc_id}")
            
            # Update original document with watermark removal info
            update_data = {
                "watermark_removal_status": watermark_status,
                "watermark_removal_completed_at": datetime.utcnow(),
                "watermark_removal_output_file": output_filename,
                "watermark_removal_output_path": output_file_path,
                "watermark_removal_output_size": output_file_size,
                "watermark_removal_message": status_message,
                "cleaned_document_id": cleaned_doc_id
            }
        else:
            watermark_status = "failed"
            update_data = {
                "watermark_removal_status": watermark_status,
                "watermark_removal_completed_at": datetime.utcnow(),
                "watermark_removal_message": status_message,
                "watermark_removal_error": status_message
            }
            logger.error(f"Watermark removal failed for doc_id={doc_id}: {status_message}")
        
        # Update original document
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": update_data}
        )
        
        # Complete job
        if success:
            complete_job(
                job_id,
                user_id,
                JobStatus.COMPLETED,
                {
                    "doc_id": doc_id,
                    "cleaned_document_id": update_data.get("cleaned_document_id"),
                },
            )
        else:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[status_message])
        
        return {
            "doc_id": doc_id,
            "status": watermark_status,
            "message": status_message,
            "cleaned_document_id": update_data.get("cleaned_document_id") if success else None
        }
    
    except SoftTimeLimitExceeded:
        error_msg = f"Watermark removal task timed out for doc_id={doc_id}"
        logger.error(error_msg)
        
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "watermark_removal_status": "failed",
                    "watermark_removal_message": error_msg,
                    "watermark_removal_completed_at": datetime.utcnow()
                }
            }
        )
        if job_id:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[error_msg])
        
        raise
    
    except Exception as e:
        error_msg = f"Unexpected error during watermark removal for doc_id={doc_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)
        
        # Update document with error status
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "watermark_removal_status": "failed",
                    "watermark_removal_message": error_msg,
                    "watermark_removal_completed_at": datetime.utcnow()
                }
            }
        )
        if job_id:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[error_msg])
        
        # Retry with exponential backoff
        retry_delay = CELERY_RETRY_BACKOFF_BASE ** self.request.retries
        raise self.retry(exc=e, countdown=retry_delay)
