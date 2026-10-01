"""
Watermark removal tasks for async processing
"""
from app.celery_config import celery_app
from app.db.mongodb import get_documents_collection
from app.utils.docker_watermark import remove_watermark_with_docker
from app.config.settings import (
    CELERY_MAX_RETRIES,
    WATERMARK_REMOVAL_OUTPUT_SUFFIX_TEMPLATE,
    convert_host_path_to_container,
)
from app.schemas import JobType, JobStatus
from app.services.job_logger import update_job_progress, complete_job
from app.services.storage_service import add_storage
from app.tasks.lifecycle import TrackedJob, handle_task_exception
from bson import ObjectId
from datetime import datetime
import logging
from pathlib import Path

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
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.WATERMARK_REMOVAL, f"Watermark Removal (mode {aggressiveness_mode})",
        {"doc_id": doc_id, "mode": aggressiveness_mode},
    )
    job_id = job.job_id

    def mark_document_failed(message: str) -> None:
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {
                "watermark_removal_status": "failed",
                "watermark_removal_message": message,
                "watermark_removal_completed_at": datetime.utcnow(),
            }},
        )

    try:
        
        logger.info(
            "Starting watermark removal for doc_id=%s, mode=%s", doc_id, aggressiveness_mode
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
        
        # Run watermark removal using Docker; the cleaned PDF is stored under
        # the id of the document record created for it
        cleaned_oid = ObjectId()
        success, status_message, output_file_info = remove_watermark_with_docker(
            doc_id=doc_id,
            user_id=user_id,
            pdf_file_path=pdf_path,
            aggressiveness_mode=aggressiveness_mode,
            output_id=str(cleaned_oid),
        )
        
        # Determine final status and prepare update
        if success:
            watermark_status = "completed"
            
            # Get file size of cleaned PDF
            output_file_path = str(convert_host_path_to_container(output_file_info.get("path")))
            output_file_size = output_file_info.get("size", 0)
            original = documents_col.find_one({"_id": ObjectId(doc_id)}, {"filename": 1}) or {}
            output_filename = (
                f"{Path(original['filename']).stem}"
                f"{WATERMARK_REMOVAL_OUTPUT_SUFFIX_TEMPLATE.format(mode=aggressiveness_mode)}"
                if original.get("filename") else output_file_info.get("filename")
            )
            # The cleaned PDF is stored in the user's workspace: it counts against the quota
            add_storage(user_id, output_file_size)
            
            logger.info(
                "Watermark removal successful for doc_id=%s: output_file=%s, size=%s", doc_id, output_filename, output_file_size
            )
            
            update_job_progress(job_id, user_id, None, 80, "Creating cleaned document record...")
            
            # Create a new document record for the cleaned PDF
            # (keeping original document intact)
            cleaned_doc_data = {
                "_id": cleaned_oid,
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
            
            logger.info("Created new document record for cleaned PDF: %s", cleaned_doc_id)
            
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
            logger.error("Watermark removal failed for doc_id=%s: %s", doc_id, status_message)
        
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
    
    except BaseException as exc:  # noqa: BLE001 - re-raised by handle_task_exception
        handle_task_exception(self, exc, job, on_final_failure=mark_document_failed)
