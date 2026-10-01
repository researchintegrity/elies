"""
Watermark removal service for handling watermark removal operations
Provides business logic for watermark removal CRUD operations
"""

from datetime import datetime
from typing import Dict

from app.db.mongodb import get_documents_collection
from app.exceptions import ValidationError
from app.services.resource_helpers import get_owned_resource
from app.schemas import JobType
from app.services.job_logger import create_job_log, ensure_job_capacity
from app.services.task_submission import submit_task
from app.tasks.watermark_removal import remove_watermark_from_document
from app.config.settings import convert_host_path_to_container
import logging

logger = logging.getLogger(__name__)


def initiate_watermark_removal(
    document_id: str,
    user_id: str,
    aggressiveness_mode: int = 2
) -> Dict:
    """
    Initiate watermark removal for a document
    
    This service:
    1. Validates the document exists and belongs to the user
    2. Validates aggressiveness mode
    3. Queues the async watermark removal task
    4. Returns task information for status tracking
    
    Args:
        document_id: Document ID to remove watermark from
        user_id: User ID (as string) who owns the document
        aggressiveness_mode: Watermark removal mode (1, 2, or 3)
                           1 = explicit watermarks only
                           2 = text + repeated graphics (default)
                           3 = all graphics (most aggressive)
        
    Returns:
        Dictionary with task info and document details
        
    Raises:
        ValidationError: Invalid mode or not a PDF
        ResourceNotFoundError: Document not found or not owned by the user
    """
    # Validate aggressiveness mode
    if aggressiveness_mode not in [1, 2, 3]:
        raise ValidationError(
            f"Invalid aggressiveness mode: {aggressiveness_mode}. Must be 1, 2, or 3."
        )

    documents_col = get_documents_collection()

    doc = get_owned_resource(get_documents_collection, document_id, user_id, "Document")
    doc_oid = doc["_id"]
    ensure_job_capacity(user_id)

    # Check if document is a PDF
    if not doc.get("file_path", "").lower().endswith(".pdf"):
        raise ValidationError("Document is not a PDF file")
    
    # Resolve the stored file path to absolute path for worker container
    pdf_path = str(convert_host_path_to_container(doc['file_path']))
    
    logger.info(
        "Initiating watermark removal for doc_id=%s, user_id=%s, mode=%s", document_id, user_id, aggressiveness_mode
    )
    
    # Create job log entry for the jobs dashboard (pending state)
    doc_name = doc.get("filename", document_id)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.WATERMARK_REMOVAL,
        title=f"Watermark Removal: {doc_name}",
        input_data={"document_id": document_id, "filename": doc_name, "mode": aggressiveness_mode}
    )
    
    # Queue async watermark removal task
    task = submit_task(remove_watermark_from_document, dict(
        doc_id=document_id,
        user_id=user_id,
        pdf_path=pdf_path,
        aggressiveness_mode=aggressiveness_mode,
        job_id=job_id
    ), owner_id=user_id, job_id=job_id)
    

    # Update document with task information
    documents_col.update_one(
        {"_id": doc_oid},
        {
            "$set": {
                "watermark_removal_task_id": task.id,
                "watermark_removal_requested_at": datetime.utcnow(),
                "watermark_removal_status": "queued",
                "watermark_removal_mode": aggressiveness_mode
            }
        }
    )
    
    logger.info("Watermark removal task queued with ID: %s", task.id)
    
    return {
        "document_id": document_id,
        "task_id": task.id,
        "status": "queued",
        "aggressiveness_mode": aggressiveness_mode,
        "message": f"Watermark removal queued with mode {aggressiveness_mode}"
    }


def get_watermark_removal_status(
    document_id: str,
    user_id: str
) -> Dict:
    """
    Get the status of watermark removal for a document
    
    Args:
        document_id: Document ID
        user_id: User ID (as string) who owns the document
        
    Returns:
        Dictionary with status information
        
    Raises:
        ResourceNotFoundError: Document not found or not owned by the user
    """
    doc = get_owned_resource(get_documents_collection, document_id, user_id, "Document")

    # Extract watermark removal information
    status = doc.get("watermark_removal_status", "not_started")
    
    return {
        "document_id": document_id,
        "status": status,
        "aggressiveness_mode": doc.get("watermark_removal_mode"),
        "started_at": doc.get("watermark_removal_started_at"),
        "completed_at": doc.get("watermark_removal_completed_at"),
        "message": doc.get("watermark_removal_message"),
        "output_filename": doc.get("watermark_removal_output_file"),
        "output_size": doc.get("watermark_removal_output_size"),
        "cleaned_document_id": doc.get("cleaned_document_id"),
        "error": doc.get("watermark_removal_error")
    }
