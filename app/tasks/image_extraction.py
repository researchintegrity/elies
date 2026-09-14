"""
Image extraction tasks for async processing
"""
from celery.exceptions import SoftTimeLimitExceeded
from app.celery_config import celery_app
from app.db.mongodb import get_documents_collection, get_images_collection
from app.utils.file_storage import figure_extraction_hook
from app.utils.metadata_parser import parse_pdf_extraction_filename, is_pdf_extraction_filename, extract_exif_metadata
from app.config.settings import CELERY_MAX_RETRIES, CELERY_RETRY_BACKOFF_BASE, convert_host_path_to_container
from app.schemas import JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from app.tasks.cbir import cbir_index_batch
from bson import ObjectId
from datetime import datetime
import logging
from pathlib import Path


logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.extract_images")
def extract_images_from_document(self, doc_id: str, user_id: str, pdf_path: str, job_id: str = None):
    """
    Extract images from PDF document asynchronously
    
    This task:
    1. Updates document status to 'processing'
    2. Calls extraction hook (your existing code)
    3. Updates MongoDB with results
    4. Retries on failure (up to 3 times)
    
    Args:
        doc_id: MongoDB document ID
        user_id: User who uploaded document
        pdf_path: Full path to PDF file
        job_id: Optional pre-created job ID from the route (for pending state tracking)
        
    Returns:
        Dict with extraction results
        
    Raises:
        Retries automatically with exponential backoff on failure
    """
    documents_col = get_documents_collection()
    
    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.IMAGE_EXTRACTION,
                title="PDF Image Extraction",
                celery_task_id=self.request.id,
                input_data={"doc_id": doc_id}
            )
        
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Starting extraction...")
        logger.info(f"Starting image extraction for doc_id={doc_id}")
        
        # Update status to processing
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "extraction_status": "processing",
                    "extraction_started_at": datetime.utcnow(),
                    "extraction_retry_count": self.request.retries
                }
            }
        )
        
        update_job_progress(job_id, user_id, None, 30, "Running PDF extraction...")
        
        # Run extraction using existing hook
        extracted_count, extraction_errors, extracted_files = figure_extraction_hook(
            doc_id=doc_id,
            user_id=user_id,
            pdf_file_path=pdf_path
        )
        
        # Determine final status
        if extraction_errors and extracted_count > 0:
            extraction_status = "completed_with_errors"
        elif extraction_errors and extracted_count == 0:
            extraction_status = "failed"
        else:
            extraction_status = "completed"
        
        logger.info(
            f"Extraction completed for doc_id={doc_id}: "
            f"extracted={extracted_count}, errors={len(extraction_errors)}\n"
        )
        
        update_job_progress(job_id, user_id, None, 60, "Processing extracted images...")
        
        # Store individual image records in images collection
        images_col = get_images_collection()
        extracted_files_with_ids = []
        if extracted_files:
            for image_file in extracted_files:
                try:
                    # Parse metadata from filename if it's a PDF extraction
                    pdf_page = None
                    page_bbox = None
                    extraction_mode = None
                    original_filename = image_file['filename']
                    
                    if is_pdf_extraction_filename(image_file['filename']):
                        metadata = parse_pdf_extraction_filename(image_file['filename'])
                        pdf_page = metadata.get('page_number')
                        page_bbox = metadata.get('bbox')
                        extraction_mode = metadata.get('extraction_mode')
                        logger.debug(
                            f"Parsed metadata from {image_file['filename']}: "
                            f"page={pdf_page}, bbox={page_bbox}, mode={extraction_mode}"
                        )
                    
                    

                    # Create image document with metadata
                    image_doc = {
                        "user_id": user_id,
                        "filename": image_file['filename'],
                        "file_path": image_file['path'],
                        "file_size": image_file['size'],
                        "source_type": "extracted",
                        "document_id": doc_id,
                        "pdf_page": pdf_page,
                        "page_bbox": page_bbox,
                        "extraction_mode": extraction_mode,
                        "original_filename": original_filename,
                        "image_type": [],  # Empty list, to be populated by panel extraction or user
                        "uploaded_date": datetime.utcnow(),
                        "exif_metadata": "Not Extracted Yet"
                    }
                    
                    # Insert document to get MongoDB _id
                    result = images_col.insert_one(image_doc)
                    image_id = result.inserted_id
                    logger.debug(f"Inserted image document with _id={image_id}")
                    
                    # The extracted images contains the bbox of their location in the PDF,
                    # we will rename each image to its MongoDB _id for uniqueness
                    
                    file_ext = Path(image_file['filename']).suffix
                    new_filename = Path(image_file['filename']).with_name(f"{image_id}{file_ext}")
                    
                    old_path = Path(image_file['path'])
                    
                    if not old_path.is_absolute():
                        old_path = Path.cwd() / old_path
                    
                    new_path = old_path.parent / new_filename

                    try:
                        old_path.rename(new_path)
                        logger.info(f"Renamed {original_filename} to {new_filename}")
                    except OSError as e:
                        logger.error(
                            f"Failed to rename {original_filename} to {new_filename}: {str(e)}",
                            exc_info=True
                        )
                        # Delete MongoDB doc since we can't rename the file
                        images_col.delete_one({"_id": image_id})
                        extraction_errors.append(
                            f"Failed to rename {original_filename} to {new_filename}: {str(e)}"
                        )
                        continue
                    
                    # Update MongoDB with new filename 
                    file_storage_path = convert_host_path_to_container(new_path)
                    
                    images_col.update_one(
                        {"_id": image_id},
                        {
                            "$set": {
                                "filename": str(new_filename),
                                "file_path": str(file_storage_path)
                            }
                        }
                    )
                    logger.debug(
                        f"Updated MongoDB: filename={new_filename}, "
                        f"file_path={file_storage_path}"
                    )
                    
                    # Add the MongoDB _id to the extracted file info for later reference
                    image_file['mongodb_id'] = str(image_id)
                    image_file['filename'] = str(new_filename)
                    image_file['path'] = str(file_storage_path)
                    extracted_files_with_ids.append(image_file)

                    # Extract EXIF metadata and update MongoDB
                    exif_metadata = extract_exif_metadata(image_file['path'])
                    images_col.update_one(
                        {"_id": image_id},
                        {
                            "$set": {
                                "exif_metadata": exif_metadata,
                            }
                        }
                    )
                    logger.debug(
                        f"Updated MongoDB: exif metadata for {new_filename}, "
                    )
                    
                except Exception as e:
                    logger.error(
                        f"Error processing extracted image {image_file['filename']}: {str(e)}",
                        exc_info=True
                    )
                    extraction_errors.append(
                        f"Error processing {image_file['filename']}: {str(e)}"
                    )
                    continue
        else:
            extracted_files_with_ids = extracted_files
        
        # Update with final results
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "extraction_status": extraction_status,
                    "extracted_image_count": extracted_count,
                    "extracted_images": extracted_files_with_ids,  # Store detailed file info with MongoDB IDs
                    "extraction_errors": extraction_errors,
                    "extraction_completed_at": datetime.utcnow()
                }
            }
        )
        
        status = "success" if extraction_status=="completed" else "failed"
        
        # Complete job
        if extraction_status == "completed":
            complete_job(
                job_id,
                user_id,
                JobStatus.COMPLETED,
                {"doc_id": doc_id, "extracted_count": extracted_count},
            )
        elif extraction_status == "completed_with_errors":
            complete_job(
                job_id,
                user_id,
                JobStatus.PARTIAL,
                {"doc_id": doc_id, "extracted_count": extracted_count},
                errors=extraction_errors,
            )
        else:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=extraction_errors)
        
        result = {
            "doc_id": doc_id,
            "status": status,
            "extracted_count": extracted_count,
            "errors": extraction_errors,
            "completed_at": datetime.utcnow().isoformat()
        }
        
        # Trigger CBIR indexing for all successfully extracted images
        if extracted_files_with_ids and extraction_status in ["completed", "completed_with_errors"]:
            try:
                cbir_items = [
                    {
                        "image_id": img_file["mongodb_id"],
                        "image_path": img_file["path"],
                        "labels": []  # No labels for extracted images initially
                    }
                    for img_file in extracted_files_with_ids
                    if img_file.get("mongodb_id")  # Only include images with valid MongoDB IDs
                ]
                if cbir_items:
                    cbir_index_batch.delay(user_id=user_id, image_items=cbir_items)
                    logger.info(f"Queued CBIR indexing for {len(cbir_items)} extracted images from doc_id={doc_id}")
            except Exception as cbir_error:
                logger.warning(f"Failed to queue CBIR indexing for doc_id={doc_id}: {cbir_error}")
                # Don't fail extraction if CBIR indexing fails to queue
        
        # If extraction failed, raise an exception to make the Celery task state FAILURE
        if extraction_status in ["failed", "completed_with_errors"]:
            error_message = f"Image extraction {extraction_status}: {extraction_errors}"
            logger.error(error_message)
            raise ValueError(error_message)
        
        return result
        
    except SoftTimeLimitExceeded:
        logger.error(f"Task timeout for doc_id={doc_id}")
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {
                "$set": {
                    "extraction_status": "failed",
                    "extraction_errors": ["Task execution timeout"]
                }
            }
        )
        if job_id:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=["Task execution timeout"])
        raise

    except Exception as exc:
        logger.error(f"Extraction error for doc_id={doc_id}: {str(exc)}", exc_info=True)
        
        # Retry with exponential backoff
        if self.request.retries >= CELERY_MAX_RETRIES:
            logger.error(f"Max retries reached for doc_id={doc_id}. Marking as failed.")
            documents_col.update_one(
                {"_id": ObjectId(doc_id)},
                {
                    "$set": {
                        "extraction_status": "failed",
                        "extraction_errors": [f"Max retries reached: {str(exc)}"]
                    }
                }
            )
            if job_id:
                complete_job(job_id, user_id, JobStatus.FAILED, errors=[f"Max retries reached: {str(exc)}"])
            raise exc
        countdown = 5 * (CELERY_RETRY_BACKOFF_BASE ** self.request.retries)
        logger.info(f"Retrying in {countdown} seconds (attempt {self.request.retries + 1}/{CELERY_MAX_RETRIES})")
        
        raise self.retry(exc=exc, countdown=countdown)
