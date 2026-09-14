"""
CBIR (Content-Based Image Retrieval) tasks for async processing

These tasks handle background indexing and searching operations.
"""
from app.celery_config import celery_app
from app.db.mongodb import (
    get_images_collection,
    get_analyses_collection,
    get_indexing_jobs_collection,
)
from app.schemas import JobStatus
from app.services.job_logger import update_job_progress as update_main_job_progress, complete_job
from app.utils.docker_cbir import (
    index_image,
    index_images_batch,
    search_similar_images,
    delete_image_from_index,
    delete_user_data,
    update_image_labels,
    check_cbir_health,
)
from app.config.settings import CELERY_MAX_RETRIES, INDEXING_BATCH_CHUNK_SIZE
from app.schemas import AnalysisStatus, IndexingJobStatus
from bson import ObjectId
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


def _cleanup_batch_images(image_items: list, user_id: str) -> list:
    """
    Delete all images in a batch when CBIR indexing fails.
    
    Uses all-or-nothing policy: if ANY image fails, ALL are deleted.
    
    Args:
        image_items: List of dicts with 'image_id' keys
        user_id: User ID who owns the images
        
    Returns:
        List of deleted image IDs
    """
    # Lazy import to avoid circular import with image_service
    from app.services.image_service import delete_image_and_artifacts
    
    deleted_ids = []
    for item in image_items:
        image_id = item.get("image_id")
        if not image_id:
            continue
        try:
            delete_image_and_artifacts(image_id=image_id, user_id=user_id)
            deleted_ids.append(image_id)
            logger.info(f"Cleaned up image {image_id} after CBIR failure")
        except Exception as cleanup_err:
            logger.error(f"Failed to cleanup image {image_id}: {cleanup_err}")
    return deleted_ids



@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_index_image")
def cbir_index_image(
    self,
    user_id: str,
    image_id: str,
    image_path: str,
    labels: list = None
):
    """
    Index a single image in the CBIR system asynchronously.
    
    Args:
        user_id: User ID for multi-tenancy
        image_id: MongoDB image ID
        image_path: Path to the image file
        labels: Optional list of labels
    """
    try:
        logger.info(f"Indexing image {image_id} for user {user_id}")
        
        success, message, result = index_image(
            user_id=user_id,
            image_path=image_path,
            labels=labels or []
        )
        
        if success:
            # Update image document with CBIR status
            images_col = get_images_collection()
            images_col.update_one(
                {"_id": ObjectId(image_id)},
                {
                    "$set": {
                        "cbir_indexed": True,
                        "cbir_indexed_at": datetime.utcnow(),
                        "cbir_id": result.get("id")
                    }
                }
            )
            logger.info(f"Image {image_id} indexed successfully")
            return {"status": "success", "cbir_id": result.get("id")}
        else:
            logger.error(f"Failed to index image {image_id}: {message}")
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error(f"Error indexing image {image_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_index_batch")
def cbir_index_batch(
    self,
    user_id: str,
    image_items: list
):
    """
    Index multiple images in batch asynchronously.
    
    Uses all-or-nothing policy: if indexing fails, all images are deleted.
    
    Args:
        user_id: User ID for multi-tenancy
        image_items: List of dicts with 'image_id', 'image_path', 'labels'
    """
    try:
        logger.info(f"Batch indexing {len(image_items)} images for user {user_id}")
        
        # Pre-check: verify CBIR is available before processing
        cbir_healthy, cbir_message = check_cbir_health()
        if not cbir_healthy:
            logger.error(f"CBIR service unavailable for batch index: {cbir_message}")
            deleted_ids = _cleanup_batch_images(image_items, user_id)
            return {
                "status": "failed",
                "error": "Unable to process images at this time.",
                "deleted_image_ids": deleted_ids
            }
        
        # Prepare items for CBIR
        cbir_items = [
            {"image_path": item["image_path"], "labels": item.get("labels", [])}
            for item in image_items
        ]
        
        success, message, result = index_images_batch(user_id, cbir_items)
        
        if success:
            # Update image documents with CBIR status
            images_col = get_images_collection()
            indexed_count = result.get("indexed_count", 0)
            
            # Mark all as indexed (CBIR handles duplicates internally)
            for item in image_items:
                images_col.update_one(
                    {"_id": ObjectId(item["image_id"])},
                    {
                        "$set": {
                            "cbir_indexed": True,
                            "cbir_indexed_at": datetime.utcnow()
                        }
                    }
                )
            
            logger.info(f"Batch indexed {indexed_count} images for user {user_id}")
            return {"status": "success", "indexed_count": indexed_count}
        else:
            # CBIR failed - delete all images in the batch
            logger.error(f"Failed to batch index for user {user_id}: {message}")
            deleted_ids = _cleanup_batch_images(image_items, user_id)
            logger.warning(f"Cleaned up {len(deleted_ids)} images after CBIR failure")
            return {
                "status": "failed", 
                "error": message,
                "deleted_image_ids": deleted_ids
            }
            
    except Exception as e:
        logger.error(f"Error batch indexing for user {user_id}: {e}")
        # Clean up images before retrying (they're gone so retry won't help)
        deleted_ids = _cleanup_batch_images(image_items, user_id)
        logger.warning(f"Cleaned up {len(deleted_ids)} images after exception")
        # Don't retry since images are deleted
        return {
            "status": "failed",
            "error": str(e),
            "deleted_image_ids": deleted_ids
        }


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_index_batch_with_progress")
def cbir_index_batch_with_progress(
    self,
    job_id: str,
    user_id: str,
    image_items: list,
    main_job_id: str = None
):
    """
    Index multiple images in batch with progress tracking.
    
    This task updates the indexing_jobs collection as it processes images,
    allowing the frontend to poll for progress.
    
    Args:
        job_id: Unique job ID for tracking (indexing_jobs collection)
        user_id: User ID for multi-tenancy
        image_items: List of dicts with 'image_id', 'image_path', 'labels'
        main_job_id: Optional job ID for the main jobs dashboard
    """
    jobs_col = get_indexing_jobs_collection()
    images_col = get_images_collection()
    total_images = len(image_items)
    
    # Terminal statuses that should not be updated
    TERMINAL_STATUSES = [
        IndexingJobStatus.COMPLETED.value,
        IndexingJobStatus.PARTIAL.value,
        IndexingJobStatus.FAILED.value
    ]
    
    def update_job_progress(
        status: str,
        processed: int,
        indexed: int,
        failed: int,
        current_step: str,
        errors: list = None,
        completed: bool = False
    ):
        """Helper to update job progress in MongoDB with conditional update"""
        update_doc = {
            "status": status,
            "processed_images": processed,
            "indexed_images": indexed,
            "failed_images": failed,
            "progress_percent": (processed / total_images * 100) if total_images > 0 else 0,
            "current_step": current_step,
            "updated_at": datetime.utcnow(),
        }
        if errors:
            update_doc["errors"] = errors
        if completed:
            update_doc["completed_at"] = datetime.utcnow()
        
        # Use conditional update to prevent overwriting terminal states
        # Only update if status is not already in a terminal state
        jobs_col.update_one(
            {"_id": job_id, "status": {"$nin": TERMINAL_STATUSES}},
            {"$set": update_doc}
        )
    
    # Initialize progress counters before try block to ensure they're defined in except
    processed_count = 0
    indexed_count = 0
    failed_count = 0
    errors = []
    
    try:
        logger.info(f"Starting batch indexing with progress for job {job_id}: {total_images} images")
        
        # Update main job to processing if provided
        if main_job_id:
            update_main_job_progress(main_job_id, user_id, JobStatus.PROCESSING, 5, "Starting batch indexing...")
        
        # Idempotency check: verify job hasn't already been completed by another task instance
        existing_job = jobs_col.find_one({"_id": job_id})
        if existing_job and existing_job.get("status") in TERMINAL_STATUSES:
            logger.warning(f"Job {job_id} already in terminal state '{existing_job.get('status')}', skipping")
            return {
                "job_id": job_id,
                "status": existing_job.get("status"),
                "message": "Job already completed by another task instance"
            }
        
        # Update status to processing
        update_job_progress(
            status=IndexingJobStatus.PROCESSING.value,
            processed=0,
            indexed=0,
            failed=0,
            current_step="Checking service availability..."
        )
        
        # Pre-check: verify CBIR is available before processing
        cbir_healthy, cbir_message = check_cbir_health()
        if not cbir_healthy:
            logger.error(f"CBIR service unavailable for job {job_id}: {cbir_message}")
            deleted_ids = _cleanup_batch_images(image_items, user_id)
            if main_job_id:
                complete_job(main_job_id, user_id, JobStatus.FAILED, errors=["CBIR service unavailable"])
            update_job_progress(
                status=IndexingJobStatus.FAILED.value,
                processed=total_images,
                indexed=0,
                failed=total_images,
                current_step="Upload failed - images have been removed.",
                errors=["Unable to process images at this time. Please try again later."],
                completed=True
            )
            return {
                "status": IndexingJobStatus.FAILED.value,
                "indexed_count": 0,
                "failed_count": total_images,
                "deleted_image_ids": deleted_ids
            }
        
        # Process in chunks for better progress granularity
        
        for i in range(0, total_images, INDEXING_BATCH_CHUNK_SIZE):
            chunk = image_items[i:i + INDEXING_BATCH_CHUNK_SIZE]
            chunk_size = len(chunk)
            
            # Update progress before processing chunk
            update_job_progress(
                status=IndexingJobStatus.PROCESSING.value,
                processed=processed_count,
                indexed=indexed_count,
                failed=failed_count,
                current_step=f"Encoding images {i + 1} to {min(i + chunk_size, total_images)} of {total_images}",
                errors=errors
            )
            
            # Prepare CBIR items for this chunk
            cbir_items = [
                {"image_path": item["image_path"], "labels": item.get("labels", [])}
                for item in chunk
            ]
            
            # Index the chunk
            success, message, result = index_images_batch(user_id, cbir_items)
            
            if success:
                chunk_indexed = result.get("indexed_count", 0)
                chunk_failed = result.get("failed_count", 0)
                
                indexed_count += chunk_indexed
                failed_count += chunk_failed
                
                # Only mark images as indexed when the entire chunk succeeded
                # The CBIR service doesn't return per-image status, so we can't
                # determine which specific images failed within a partial chunk
                if chunk_failed == 0:
                    for item in chunk:
                        images_col.update_one(
                            {"_id": ObjectId(item["image_id"])},
                            {
                                "$set": {
                                    "cbir_indexed": True,
                                    "cbir_indexed_at": datetime.utcnow()
                                }
                            }
                        )
                else:
                    # Partial chunk failure - ALL-OR-NOTHING: clean up entire batch
                    logger.warning(
                        f"Chunk had partial failures: {chunk_indexed} indexed, {chunk_failed} failed. "
                        f"All-or-nothing policy: deleting all images in batch."
                    )
                    errors.append(
                        "Some images could not be processed. All images have been removed."
                    )
                    # Break out and go to cleanup
                    failed_count = total_images
                    break
            else:
                # Entire chunk failed - ALL-OR-NOTHING: clean up entire batch
                failed_count = total_images
                errors.append("Upload could not be completed. All images have been removed.")
                logger.error(f"Chunk indexing failed for job {job_id}: {message}")
                # Break out and go to cleanup
                break
            
            processed_count += chunk_size
        
        # Determine final status and handle cleanup
        if failed_count == 0:
            final_status = IndexingJobStatus.COMPLETED.value
            final_step = f"Successfully indexed {indexed_count} images"
            deleted_ids = []
            # Update main job as completed
            if main_job_id:
                complete_job(
                    main_job_id, user_id, JobStatus.COMPLETED,
                    output_data={"indexed_count": indexed_count, "image_count": total_images}
                )
        else:
            # ALL-OR-NOTHING: Any failure means delete ALL images
            final_status = IndexingJobStatus.FAILED.value
            deleted_ids = _cleanup_batch_images(image_items, user_id)
            errors.append("Upload failed. Please try again when the service is available.")
            final_step = "Upload failed - images have been removed."
            logger.warning(f"Job {job_id}: Cleaned up {len(deleted_ids)} images after failure")
            # Update main job as failed
            if main_job_id:
                complete_job(main_job_id, user_id, JobStatus.FAILED, errors=errors)
        
        # Final update
        update_job_progress(
            status=final_status,
            processed=total_images,
            indexed=indexed_count if failed_count == 0 else 0,
            failed=failed_count,
            current_step=final_step,
            errors=errors,
            completed=True
        )
        
        logger.info(f"Job {job_id} completed: {final_step}")
        return {
            "status": final_status,
            "indexed_count": indexed_count if failed_count == 0 else 0,
            "failed_count": failed_count,
            "deleted_image_ids": deleted_ids
        }
        
    except Exception as e:
        logger.error(f"Error in batch indexing job {job_id}: {e}")
        
        # Update main job as failed
        if main_job_id:
            complete_job(main_job_id, user_id, JobStatus.FAILED, errors=[str(e)])
        
        # ALL-OR-NOTHING: Clean up all images on exception
        deleted_ids = _cleanup_batch_images(image_items, user_id)
        logger.warning(f"Job {job_id}: Cleaned up {len(deleted_ids)} images after exception")
        
        # Update job as failed with cleanup info
        update_job_progress(
            status=IndexingJobStatus.FAILED.value,
            processed=total_images,
            indexed=0,
            failed=total_images,
            current_step="Upload failed - images have been removed.",
            errors=["An unexpected error occurred. Please try again."],
            completed=True
        )
        
        # Don't retry - images are already deleted
        return {
            "status": IndexingJobStatus.FAILED.value,
            "indexed_count": 0,
            "failed_count": total_images,
            "deleted_image_ids": deleted_ids,
            "error": str(e)
        }


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_search")
def cbir_search(
    self,
    analysis_id: str,
    user_id: str,
    query_image_id: str,
    query_image_path: str,
    top_k: int = 10,
    labels: list = None
):
    """
    Search for similar images asynchronously.
    
    Args:
        analysis_id: MongoDB analysis ID for tracking
        user_id: User ID for multi-tenancy
        query_image_id: MongoDB ID of query image
        query_image_path: Path to query image
        top_k: Number of results
        labels: Optional filter labels
    """
    analyses_col = get_analyses_collection()
    
    try:
        # Update status to processing
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.PROCESSING,
                    "status_message": "Searching for similar images...",
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        logger.info(f"Searching similar images for analysis {analysis_id}")
        
        success, message, results = search_similar_images(
            user_id=user_id,
            image_path=query_image_path,
            top_k=top_k,
            labels=labels
        )
        
        if success:
            # Enrich results with image IDs from our database
            enriched_results = _enrich_search_results(user_id, results)
            
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.COMPLETED,
                        "status_message": "Completed",
                        "results": {
                            "timestamp": datetime.utcnow(),
                            "query_image_id": query_image_id,
                            "top_k": top_k,
                            "labels_filter": labels,
                            "matches_count": len(enriched_results),
                            "matches": enriched_results
                        },
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            logger.info(f"CBIR search completed for analysis {analysis_id}, found {len(enriched_results)} matches")
            return {"status": "completed", "matches_count": len(enriched_results)}
        else:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": message,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            logger.error(f"CBIR search failed for analysis {analysis_id}: {message}")
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error(f"Error in CBIR search for analysis {analysis_id}: {e}")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.FAILED,
                    "error": str(e),
                    "updated_at": datetime.utcnow()
                }
            }
        )
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_delete_image")
def cbir_delete_image(
    self,
    user_id: str,
    image_id: str,
    image_path: str
):
    """
    Delete an image from the CBIR index asynchronously.
    
    Args:
        user_id: User ID for multi-tenancy
        image_id: MongoDB image ID
        image_path: Path to the image
    """
    try:
        logger.info(f"Deleting image {image_id} from CBIR index")
        
        success, message = delete_image_from_index(user_id, image_path)
        
        if success:
            # Update image document
            images_col = get_images_collection()
            images_col.update_one(
                {"_id": ObjectId(image_id)},
                {
                    "$set": {
                        "cbir_indexed": False,
                        "cbir_id": None
                    },
                    "$unset": {
                        "cbir_indexed_at": ""
                    }
                }
            )
            logger.info(f"Image {image_id} removed from CBIR index")
            return {"status": "success"}
        else:
            logger.error(f"Failed to delete image {image_id} from CBIR: {message}")
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error(f"Error deleting image {image_id} from CBIR: {e}")
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_update_labels")
def cbir_update_labels(
    self,
    user_id: str,
    image_id: str,
    image_path: str,
    labels: list
):
    """
    Update labels for an image in the CBIR index asynchronously.
    
    This is called when user modifies image_type tags to keep
    MongoDB and MilvusDB in sync.
    
    Args:
        user_id: User ID for multi-tenancy
        image_id: MongoDB image ID
        image_path: Path to the image
        labels: New labels list
    """
    try:
        logger.info(f"Updating CBIR labels for image {image_id}: {labels}")
        
        success, message = update_image_labels(user_id, image_path, labels)
        
        if success:
            logger.info(f"CBIR labels updated for image {image_id}")
            return {"status": "success", "labels": labels}
        else:
            logger.warning(f"CBIR label update for image {image_id}: {message}")
            return {"status": "skipped", "message": message}
            
    except Exception as e:
        logger.error(f"Error updating CBIR labels for image {image_id}: {e}")
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_delete_user_data")
def cbir_delete_user_data(self, user_id: str):
    """
    Delete all CBIR data for a user asynchronously.
    
    Args:
        user_id: User ID whose data to delete
    """
    try:
        logger.info(f"Deleting all CBIR data for user {user_id}")
        
        success, message = delete_user_data(user_id)
        
        if success:
            # Update all user's images
            images_col = get_images_collection()
            images_col.update_many(
                {"user_id": user_id},
                {
                    "$set": {"cbir_indexed": False, "cbir_id": None},
                    "$unset": {"cbir_indexed_at": ""}
                }
            )
            logger.info(f"All CBIR data deleted for user {user_id}")
            return {"status": "success"}
        else:
            logger.error(f"Failed to delete CBIR data for user {user_id}: {message}")
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error(f"Error deleting CBIR data for user {user_id}: {e}")
        raise self.retry(exc=e, countdown=60)


def _enrich_search_results(user_id: str, results: list) -> list:
    """
    Enrich CBIR search results with image data from MongoDB.
    
    Args:
        user_id: User ID
        results: Raw CBIR results
        
    Returns:
        Enriched results
    """
    if not results:
        return []
    
    images_col = get_images_collection()
    
    # Get all image paths
    paths = [r["image_path"] for r in results]
    
    # Query images by path
    images = list(images_col.find({
        "user_id": user_id,
        "file_path": {"$in": paths}
    }))
    
    # Create lookup by path
    path_to_image = {img["file_path"]: img for img in images}
    
    enriched = []
    for result in results:
        path = result["image_path"]
        image = path_to_image.get(path)
        
        # Note: For Inner Product (IP) metric, distance IS the similarity score (higher = more similar)
        # Our CBIR uses IP metric with normalized embeddings, so distance is cosine similarity
        raw_distance = result.get("distance", 0)
        similarity = max(0.0, min(1.0, raw_distance))  # Clamp to [0, 1] range
        
        enriched_result = {
            "cbir_id": result.get("id"),
            "distance": raw_distance,
            "similarity_score": round(similarity, 4),
            "cbir_labels": result.get("labels", []),
            "image_path": path,
        }
        
        if image:
            enriched_result.update({
                "image_id": str(image["_id"]),
                "filename": image.get("filename"),
                "file_size": image.get("file_size"),
                "source_type": image.get("source_type"),
                "document_id": image.get("document_id"),
                "image_type": image.get("image_type", []),
            })
        
        enriched.append(enriched_result)
    
    return enriched
