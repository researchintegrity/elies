"""
Panel extraction tasks for async processing
"""
import os
import logging
from typing import Dict, List, Any
from datetime import datetime
from celery.exceptions import SoftTimeLimitExceeded
from bson import ObjectId
from app.celery_config import celery_app
from app.db.mongodb import get_images_collection
from app.utils.docker_panel_extractor import extract_panels_with_docker
from app.config.settings import (
    CELERY_MAX_RETRIES, 
    CELERY_RETRY_BACKOFF_BASE,
    convert_container_path_to_host,
    convert_host_path_to_container
)
from app.schemas import JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from app.tasks.cbir import cbir_index_batch

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.extract_panels")
def extract_panels_from_images(
    self,
    image_ids: List[str],
    user_id: str,
    image_paths: List[str],
    job_id: str = None
):
    """
    Extract panels from images asynchronously

    This task:
    1. Validates all images exist and belong to user
    2. Calls Docker panel extraction
    3. Parses PANELS.csv output
    4. Creates MongoDB documents for each extracted panel
    5. Stores panel images in workspace
    6. Retries on failure (up to 3 times)

    Args:
        image_ids: List of MongoDB image document IDs
        user_id: User who owns the images
        image_paths: List of full paths to image files
        job_id: Optional pre-created job ID from the service (for pending state tracking)

    Returns:
        Dict with panel extraction results {
            task_id, status, image_ids, extracted_panels_count, result_panel_ids
        }

    Raises:
        Retries automatically with exponential backoff on failure
    """
    images_col = get_images_collection()
    task_id = self.request.id

    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.PANEL_EXTRACTION,
                title=f"Panel Extraction ({len(image_ids)} images)",
                celery_task_id=task_id,
                input_data={"image_ids": image_ids}
            )
        
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Validating images...")
        logger.info(
            f"Starting panel extraction for user_id={user_id}, "
            f"task_id={task_id}, image_count={len(image_ids)}"
        )

        # Validate all images exist and belong to user
        for img_id in image_ids:
            try:
                image_doc = images_col.find_one({"_id": ObjectId(img_id), "user_id": user_id})
                if not image_doc:
                    error_msg = f"Image not found or does not belong to user: {img_id}"
                    logger.error(error_msg)
                    return _handle_panel_extraction_failure(
                        task_id, image_ids, user_id, error_msg
                    )
            except Exception as e:
                error_msg = f"Error validating image {img_id}: {str(e)}"
                logger.error(error_msg)
                return _handle_panel_extraction_failure(
                    task_id, image_ids, user_id, error_msg
                )

        # Validate image files exist
        for img_path in image_paths:
            if not os.path.exists(img_path):
                error_msg = f"Image file not found: {img_path}"
                logger.error(error_msg)
                return _handle_panel_extraction_failure(
                    task_id, image_ids, user_id, error_msg
                )

        update_job_progress(job_id, user_id, None, 30, "Running Docker panel extraction...")
        
        # Run panel extraction using Docker
        success, status_message, output_info = extract_panels_with_docker(
            image_ids=image_ids,
            user_id=user_id,
            image_paths=image_paths
        )

        if not success:
            logger.error(f"Panel extraction failed: {status_message}")
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[status_message])
            return _handle_panel_extraction_failure(
                task_id, image_ids, user_id, status_message
            )

        # Parse PANELS.csv and create MongoDB documents
        panels_data = output_info.get("panels_data", [])
        panels_count = len(panels_data)
        output_dir = output_info.get("output_dir")

        update_job_progress(job_id, user_id, None, 60, f"Processing {panels_count} extracted panels...")
        logger.info(f"Panel extraction completed. Processing {panels_count} panels...")

        result_panel_ids = []

        for panel_info in panels_data:
            try:
                # Create MongoDB document for this panel
                panel_doc = _create_panel_document(
                    panel_info=panel_info,
                    user_id=user_id,
                    output_dir=output_dir
                )

                # Insert into MongoDB
                result = images_col.insert_one(panel_doc)
                panel_mongodb_id = result.inserted_id
                panel_id_str = str(panel_mongodb_id)
                result_panel_ids.append(panel_id_str)

                logger.info(f"Created panel document: {panel_id_str} from {panel_info['panel_id']}")
                
                # ============================================================
                # STEP 1: Rename panel file to use MongoDB _id
                # ============================================================
                try:
                    # Get original filename and directory
                    panel_doc_file = images_col.find_one({"_id": panel_mongodb_id})
                    original_file_path = panel_doc_file.get("file_path")
                    
                    # Convert host path to container path
                    full_old_path = convert_host_path_to_container(original_file_path)
                    
                    # New filename using _id
                    file_ext = os.path.splitext(panel_doc_file.get("filename"))[1]
                    new_filename = f"{panel_mongodb_id}{file_ext}"
                    full_new_path = os.path.join(os.path.dirname(full_old_path), new_filename)
                    
                    # Rename file
                    os.rename(full_old_path, full_new_path)
                    
                    # Update MongoDB with new path
                    stored_path = str(convert_host_path_to_container(full_new_path))
                    
                    images_col.update_one(
                        {"_id": panel_mongodb_id},
                        {
                            "$set": {
                                "filename": new_filename,
                                "file_path": stored_path
                            }
                        }
                    )
                    logger.debug(f"Renamed panel file to {new_filename}")
                    
                except Exception as e:
                    logger.error(f"Failed to rename panel file for {panel_id_str}: {str(e)}", exc_info=True)
                
                # ============================================================
                # STEP 2: Merge panel_type into source_image.image_type
                # ============================================================
                try:
                    source_image_id = panel_info.get("image_id")
                    panel_type = panel_info.get("panel_type")
                    
                    # Get source image document
                    source_image = images_col.find_one(
                        {"_id": ObjectId(source_image_id), "user_id": user_id}
                    )
                    
                    if source_image:
                        # Get existing types
                        existing_types = source_image.get("image_type", [])
                        
                        # Merge with panel_type (union, no duplicates)
                        if panel_type and panel_type not in existing_types:
                            merged_types = existing_types + [panel_type]
                            merged_types.sort()  # Sort for consistency
                            
                            # Update source image
                            images_col.update_one(
                                {"_id": ObjectId(source_image_id)},
                                {"$set": {"image_type": merged_types}}
                            )
                            logger.debug(
                                f"Propagated panel_type '{panel_type}' to source image {source_image_id}: "
                                f"{existing_types} → {merged_types}"
                            )
                    else:
                        logger.warning(f"Source image not found: {source_image_id}")
                        
                except Exception as e:
                    logger.error(
                        f"Failed to propagate panel_type for panel {panel_id_str}: {str(e)}", 
                        exc_info=True
                    )

            except Exception as e:
                error_msg = f"Error creating panel document for {panel_info.get('panel_id')}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                # Continue processing other panels instead of failing entirely

        if not result_panel_ids:
            error_msg = "No panel documents were successfully created"
            logger.error(error_msg)
            return _handle_panel_extraction_failure(
                task_id, image_ids, user_id, error_msg
            )

        # Clean up PANELS.csv after processing
        panels_csv_path = output_info.get("panels_csv_path")
        if panels_csv_path and os.path.exists(panels_csv_path):
            try:
                os.remove(panels_csv_path)
                logger.info(f"Deleted PANELS.csv: {panels_csv_path}")
            except Exception as e:
                logger.warning(f"Failed to delete PANELS.csv {panels_csv_path}: {str(e)}")

        # Trigger CBIR indexing for all successfully created panels
        if result_panel_ids:
            try:
                # Get panel documents with their paths and types
                panel_docs = list(images_col.find(
                    {"_id": {"$in": [ObjectId(pid) for pid in result_panel_ids]}},
                    {"_id": 1, "file_path": 1, "panel_type": 1}
                ))
                
                cbir_items = [
                    {
                        "image_id": str(doc["_id"]),
                        "image_path": doc["file_path"],
                        "labels": [doc.get("panel_type")] if doc.get("panel_type") else []
                    }
                    for doc in panel_docs
                ]
                
                if cbir_items:
                    cbir_index_batch.delay(user_id=user_id, image_items=cbir_items)
                    logger.info(f"Queued CBIR indexing for {len(cbir_items)} extracted panels")
            except Exception as cbir_error:
                logger.warning(f"Failed to queue CBIR indexing for panels: {cbir_error}")
                # Don't fail panel extraction if CBIR indexing fails to queue

        # Success
        complete_job(job_id, user_id, JobStatus.COMPLETED, {
            "extracted_panels_count": panels_count,
            "result_panel_ids_count": len(result_panel_ids)
        })
        
        result = {
            "task_id": task_id,
            "status": "completed",
            "image_ids": image_ids,
            "extracted_panels_count": panels_count,
            "result_panel_ids": result_panel_ids,
            "message": f"Panel extraction successful. Created {len(result_panel_ids)} panel documents",
            "error": None
        }

        logger.info(f"Panel extraction completed: {result['message']}")
        return result

    except SoftTimeLimitExceeded:
        error_msg = f"Panel extraction task timed out for user_id={user_id}"
        logger.error(error_msg)
        if job_id:
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[error_msg])
        return _handle_panel_extraction_failure(task_id, image_ids, user_id, error_msg)

    except Exception as e:
        error_msg = f"Unexpected error during panel extraction for user_id={user_id}: {str(e)}"
        logger.error(error_msg, exc_info=True)

        # Retry with exponential backoff
        retry_delay = CELERY_RETRY_BACKOFF_BASE ** self.request.retries
        try:
            raise self.retry(exc=e, countdown=retry_delay)
        except self.MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for panel extraction task {task_id}")
            if job_id:
                complete_job(job_id, user_id, JobStatus.FAILED, errors=[error_msg])
            return _handle_panel_extraction_failure(task_id, image_ids, user_id, error_msg)


def _create_panel_document(
    panel_info: Dict[str, Any],
    user_id: str,
    output_dir: str
) -> Dict[str, Any]:
    """Create a MongoDB document for an extracted panel.

    Panel images are organized in the workspace as:
    {<workspace-env>}/{user_id}/images/panels/{figname}/{_id}.{ext}
    
    Note: figname (from PANELS.csv) is the source_image_id

    Renames the panel file to use MongoDB _id after insertion.
    Also merges panel_type into source image's image_type list.

    Args:
        panel_info: Dict with panel data from PANELS.csv parsing
        user_id: User who owns this panel
        output_dir: Central panels directory where Docker outputs files
                   Format: {<workspace-env>}/{user_id}/images/panels
    Returns:
        MongoDB document ready to insert
    """
    panel_id = panel_info["panel_id"]
    image_id = panel_info["image_id"]
    panel_type = panel_info["panel_type"]
    bbox = panel_info["bbox"]
    figname = panel_info["figname"]

    # Docker outputs files as: {figname}_{panel_id}_{panel_type}.png
    # e.g., 1763555202_fig1_2_Blots.png
    panel_filename = f"{figname}_{panel_id}_{panel_type}.png"
    
    # Docker outputs to temp location in output_dir root
    temp_panel_path = os.path.join(output_dir, panel_filename)
    
    # Build organized location: {output_dir}/{figname}/
    # Note: figname IS the source_image_id in PANELS.csv, so no need to add image_id again
    organized_panel_dir = os.path.join(
        output_dir,
        figname
    )
    os.makedirs(organized_panel_dir, exist_ok=True)
    
    # Final organized path (before _id rename)
    organized_panel_path = os.path.join(organized_panel_dir, panel_filename)
    file_size = 0
    
    if os.path.exists(temp_panel_path):
        # Move the file to organized location
        try:
            import shutil
            shutil.move(temp_panel_path, organized_panel_path)
            file_size = os.path.getsize(organized_panel_path)
            logger.info(f"Organized panel file: {temp_panel_path} → {organized_panel_path}")
        except Exception as e:
            logger.error(f"Error organizing panel file: {str(e)}")
            # Fall back to temp location if move fails
            organized_panel_path = temp_panel_path
            if os.path.exists(temp_panel_path):
                file_size = os.path.getsize(temp_panel_path)
    elif os.path.exists(organized_panel_path):
        # Already in organized location
        file_size = os.path.getsize(organized_panel_path)
    else:
        logger.warning(f"Panel file not found: {temp_panel_path} or {organized_panel_path}")

    # Convert container path to host path for storage in MongoDB
    final_file_path = str(convert_host_path_to_container(organized_panel_path))
    logger.debug(f"Container path: {organized_panel_path} → Host path: {final_file_path}")

    # Fetch source image to get EXIF metadata
    images_col = get_images_collection()
    source_image = images_col.find_one({"_id": ObjectId(image_id)})
    exif_metadata = source_image.get("exif_metadata") if source_image else None

    panel_doc = {
        "user_id": user_id,
        "filename": panel_filename,  # Will be renamed after insertion
        "file_path": final_file_path,
        "file_size": file_size,
        "source_type": "panel",
        "source_image_id": image_id,
        "panel_id": panel_id,
        "panel_type": panel_type,
        "bbox": bbox,
        "image_type": [panel_type] if panel_type else [],  # Initialize with panel_type
        "uploaded_date": datetime.utcnow(),
        "created_at": datetime.utcnow(),
        "exif_metadata": exif_metadata
    }
    
    # IMPORTANT: The calling code will handle:
    # 1. MongoDB insertion to get _id
    # 2. File rename from {original_filename} to {_id}.png
    # 3. Type propagation to source image

    return panel_doc


def _handle_panel_extraction_failure(
    task_id: str,
    image_ids: List[str],
    user_id: str,
    error_message: str
) -> Dict[str, Any]:
    """Handle panel extraction failure.

    Args:
        task_id: Celery task ID
        image_ids: Image IDs that were being processed
        user_id: User ID
        error_message: Error message

    Returns:
        Standardized failure response dict
    """
    logger.error(f"Panel extraction failed for user {user_id}: {error_message}")

    return {
        "task_id": task_id,
        "status": "failed",
        "image_ids": image_ids,
        "extracted_panels_count": 0,
        "result_panel_ids": [],
        "message": f"Panel extraction failed",
        "error": error_message
    }
