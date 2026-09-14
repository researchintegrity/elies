"""
Document service for handling document operations.

Provides business logic for document CRUD operations.
"""
import logging
from pathlib import Path

from bson import ObjectId

from app.db.mongodb import (
    get_documents_collection,
    get_dual_annotations_collection,
    get_images_collection,
    get_single_annotations_collection,
)
from app.exceptions import (
    FileOperationError,
    ResourceNotFoundError,
    ValidationError,
)
from app.schemas import JobType, JobStatus
from app.services.job_logger import create_job_log, complete_job
from app.tasks.cbir import cbir_delete_image
from app.utils.file_storage import (
    delete_directory,
    delete_file,
    update_user_storage_in_db,
)
logger = logging.getLogger(__name__)


async def delete_document_and_artifacts(
    document_id: str,
    user_id: str
) -> dict:
    """
    Delete a document and all its associated artifacts (files, images, annotations).
    
    This is the single source of truth for document deletion logic.
    Called by both REST endpoints and can be imported by other services.
    
    Args:
        document_id: Document ID to delete.
        user_id: User ID (as string) who owns the document.
        
    Returns:
        Dictionary with deletion results.
        
    Raises:
        ValidationError: If document ID format is invalid.
        ResourceNotFoundError: If document not found.
        FileOperationError: If file deletion fails.
    """
    documents_col = get_documents_collection()
    images_col = get_images_collection()
    
    # Verify document belongs to user
    try:
        doc_oid = ObjectId(document_id)
    except Exception:
        raise ValidationError("Invalid document ID format")
    
    doc = documents_col.find_one({
        "_id": doc_oid,
        "user_id": user_id
    })
    
    if not doc:
        raise ResourceNotFoundError("Document", document_id)
    
    # Create job log for tracking this deletion (batched operation)
    doc_name = doc.get("original_filename", document_id)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.DOCUMENT_DELETION,
        title=f"Deleting document: {doc_name}",
        input_data={"document_id": document_id, "filename": doc_name}
    )
    
    try:
        # Delete PDF file from disk
        file_path = doc["file_path"]
    
        # In TEST environment, we need to convert container path back to host path
        from app.config.settings import RUNNING_ENV, convert_container_path_to_host
        if RUNNING_ENV == "TEST":
            try:
                file_path = convert_container_path_to_host(file_path)
            except ValueError:
                # If conversion fails, try using original path
                pass

        success, error = delete_file(file_path)

        if not success:
            raise FileOperationError("delete", str(file_path), error)
        
        # Delete extraction directory
        # Use the (potentially converted) file_path to derive the extraction directory
        # ensuring it targets the correct location in both TEST and PROD environments
        extraction_dir = Path(file_path).parent.parent / Path(f"images/extracted/{document_id}")
        success, error = delete_directory(str(extraction_dir))
        # Note: Directory might not exist, that's OK - we still proceed with DB cleanup
        if not success and "Directory not found" not in error:
            raise FileOperationError("delete", str(extraction_dir), error)
        
        # Get all extracted image IDs for this document (with full data for CBIR cleanup)
        extracted_images = list(images_col.find({
            "document_id": document_id,
            "user_id": user_id,
            "source_type": "extracted"
        }, {"_id": 1, "file_path": 1, "cbir_indexed": 1}))
        
        image_ids = [str(img["_id"]) for img in extracted_images]
        
        # Queue CBIR deletion for indexed images
        cbir_deletion_count = 0
        for img in extracted_images:
            if img.get("cbir_indexed"):
                try:
                    cbir_delete_image.delay(
                        user_id=user_id,
                        image_id=str(img["_id"]),
                        image_path=img["file_path"]
                    )
                    cbir_deletion_count += 1
                except Exception as e:
                    logger.warning(f"Failed to queue CBIR deletion for image {img['_id']}: {e}")
                    # Continue with document deletion even if CBIR deletion fails to queue
        
        if cbir_deletion_count > 0:
            logger.info(f"Queued CBIR deletion for {cbir_deletion_count} images from document {document_id}")
        
        # Delete annotations for all extracted images from both collections
        single_annotations_col = get_single_annotations_collection()
        dual_annotations_col = get_dual_annotations_collection()
        annotations_deleted = 0
        if image_ids:
            # Delete single-image annotations
            result = single_annotations_col.delete_many({
                "image_id": {"$in": image_ids},
                "user_id": user_id
            })
            annotations_deleted += result.deleted_count
            
            # Delete dual-image annotations
            result = dual_annotations_col.delete_many({
                "user_id": user_id,
                "$or": [
                    {"source_image_id": {"$in": image_ids}},
                    {"target_image_id": {"$in": image_ids}}
                ]
            })
            annotations_deleted += result.deleted_count
        
        # Delete extracted images from MongoDB
        images_deleted_result = images_col.delete_many({
            "document_id": document_id,
            "user_id": user_id,
            "source_type": "extracted"
        })
        
        # Delete document record
        documents_col.delete_one({"_id": doc_oid})
        
        # Update user storage in database
        update_user_storage_in_db(user_id)
        
        result = {
            "deleted_id": document_id,
            "annotations_deleted": annotations_deleted,
            "images_deleted": images_deleted_result.deleted_count
        }
        
        # Mark job as completed
        complete_job(
            job_id=job_id,
            user_id=user_id,
            status=JobStatus.COMPLETED,
            output_data=result
        )
        
        return result
        
    except Exception as e:
        # Mark job as failed
        complete_job(
            job_id=job_id,
            user_id=user_id,
            status=JobStatus.FAILED,
            errors=[str(e)]
        )
        raise
