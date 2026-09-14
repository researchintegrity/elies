"""
Watermark removal service for handling watermark removal operations
Provides business logic for watermark removal CRUD operations
"""

from bson import ObjectId
from typing import Dict
from app.db.mongodb import get_documents_collection
from app.schemas import JobType
from app.services.job_logger import create_job_log
from app.tasks.watermark_removal import remove_watermark_from_document
from app.config.settings import convert_host_path_to_container
import logging

logger = logging.getLogger(__name__)


async def initiate_watermark_removal(
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
        ValueError: If document not found, validation fails, or mode invalid
    """
    documents_col = get_documents_collection()
    
    # Validate aggressiveness mode
    if aggressiveness_mode not in [1, 2, 3]:
        raise ValueError(
            f"Invalid aggressiveness mode: {aggressiveness_mode}. Must be 1, 2, or 3."
        )
    
    # Verify document exists and belongs to user
    try:
        doc_oid = ObjectId(document_id)
    except Exception:
        raise ValueError("Invalid document ID format")
    
    doc = documents_col.find_one({
        "_id": doc_oid,
        "user_id": user_id
    })
    
    if not doc:
        raise ValueError("Document not found")
    
    # Check if document is a PDF
    if not doc.get("file_path", "").lower().endswith(".pdf"):
        raise ValueError("Document is not a PDF file")
    
    # Resolve the stored file path to absolute path for worker container
    pdf_path = str(convert_host_path_to_container(doc['file_path']))
    
    logger.info(
        f"Initiating watermark removal for doc_id={document_id}, "
        f"user_id={user_id}, mode={aggressiveness_mode}"
    )
    
    # Create job log entry for the jobs dashboard (pending state)
    doc_name = doc.get("original_filename", document_id)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.WATERMARK_REMOVAL,
        title=f"Watermark Removal: {doc_name}",
        input_data={"document_id": document_id, "filename": doc_name, "mode": aggressiveness_mode}
    )
    
    # Queue async watermark removal task
    task = remove_watermark_from_document.delay(
        doc_id=document_id,
        user_id=user_id,
        pdf_path=pdf_path,
        aggressiveness_mode=aggressiveness_mode,
        job_id=job_id
    )
    
    # Update document with task information
    documents_col.update_one(
        {"_id": doc_oid},
        {
            "$set": {
                "watermark_removal_task_id": task.id,
                "watermark_removal_requested_at": __import__("datetime").datetime.utcnow(),
                "watermark_removal_status": "queued",
                "watermark_removal_mode": aggressiveness_mode
            }
        }
    )
    
    logger.info(f"Watermark removal task queued with ID: {task.id}")
    
    return {
        "document_id": document_id,
        "task_id": task.id,
        "status": "queued",
        "aggressiveness_mode": aggressiveness_mode,
        "message": f"Watermark removal queued with mode {aggressiveness_mode}"
    }


async def get_watermark_removal_status(
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
        ValueError: If document not found
    """
    documents_col = get_documents_collection()
    
    # Verify document exists and belongs to user
    try:
        doc_oid = ObjectId(document_id)
    except Exception:
        raise ValueError("Invalid document ID format")
    
    doc = documents_col.find_one({
        "_id": doc_oid,
        "user_id": user_id
    })
    
    if not doc:
        raise ValueError("Document not found")
    
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
