"""
CBIR (Content-Based Image Retrieval) tasks for async processing

These tasks handle background indexing and searching operations.
"""
from app.celery_config import celery_app
from app.db.mongodb import (
    get_images_collection,
    get_indexing_jobs_collection,
)
from app.schemas import JobStatus
from app.services.job_logger import update_job_progress as update_main_job_progress, complete_job
from app.utils.docker_cbir import (
    check_cbir_health,
    check_images_indexed,
    delete_image_from_index,
    delete_user_data,
    index_image,
    index_images_batch,
    search_similar_images,
    update_image_labels,
)
from app.config.settings import CELERY_MAX_RETRIES, INDEXING_BATCH_CHUNK_SIZE
from app.schemas import IndexingJobStatus
from app.tasks.lifecycle import TrackedJob, run_analysis
from bson import ObjectId
from datetime import datetime
from typing import List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# Base delay (seconds) before retrying when CBIR is unreachable; doubles each retry
CBIR_RETRY_BASE_DELAY = 30

# Shown to users when indexing fails: the images themselves are kept
NOT_INDEXED_MESSAGE = (
    "{count} image(s) could not be indexed for similarity search. They are kept in "
    "your gallery and can be re-indexed later."
)


def _object_ids(image_ids: List[str]) -> List[ObjectId]:
    return [ObjectId(image_id) for image_id in image_ids if image_id and ObjectId.is_valid(image_id)]


def _mark_indexed(image_ids: List[str]) -> None:
    if not image_ids:
        return
    get_images_collection().update_many(
        {"_id": {"$in": _object_ids(image_ids)}},
        {"$set": {"cbir_indexed": True, "cbir_indexed_at": datetime.utcnow()},
         "$unset": {"cbir_error": "", "cbir_failed_at": ""}},
    )


def _mark_index_failure(image_ids: List[str], error: str) -> None:
    """
    Record that images could not be indexed. The images are kept: an outage of
    the CBIR service must never delete user data (issue #59).
    """
    if not image_ids:
        return
    get_images_collection().update_many(
        {"_id": {"$in": _object_ids(image_ids)}},
        {"$set": {"cbir_indexed": False, "cbir_error": error, "cbir_failed_at": datetime.utcnow()}},
    )


def _index_chunk(user_id: str, items: list) -> Tuple[List[str], List[str], Optional[str]]:
    """
    Index a chunk of images and work out which ones succeeded.

    Returns (indexed_ids, failed_ids, error_message).
    """
    ids = [item["image_id"] for item in items]
    cbir_items = [{"image_path": item["image_path"], "labels": item.get("labels", [])} for item in items]
    success, message, result = index_images_batch(user_id, cbir_items)
    if not success:
        return [], ids, message
    if result.get("failed_count", 0) == 0:
        return ids, [], None

    # Partial failure: ask CBIR which images made it into the index
    checked, check_message, visibility = check_images_indexed(user_id, [item["image_path"] for item in items])
    if not checked:
        return [], ids, f"Some images failed to index ({check_message})"
    indexed = [item["image_id"] for item in items if visibility.get(item["image_path"])]
    failed = [item["image_id"] for item in items if not visibility.get(item["image_path"])]
    return indexed, failed, "Some images failed to index"


def _retry_while_cbir_down(task, cbir_message: str) -> None:
    """Retry the task with exponential backoff while retries remain."""
    if task.request.retries < task.max_retries:
        countdown = CBIR_RETRY_BASE_DELAY * (2 ** task.request.retries)
        logger.warning("CBIR unavailable (%s); retrying in %ss", cbir_message, countdown)
        raise task.retry(countdown=countdown)


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

    Failures are recorded on the image (cbir_indexed=False, cbir_error); the
    image itself is never deleted.
    """
    try:
        logger.info("Indexing image %s for user %s", image_id, user_id)
        success, message, result = index_image(
            user_id=user_id,
            image_path=image_path,
            labels=labels or []
        )
    except Exception as e:
        logger.error("Error indexing image %s: %s", image_id, e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=CBIR_RETRY_BASE_DELAY * (2 ** self.request.retries))
        _mark_index_failure([image_id], f"Indexing error: {e}")
        return {"status": "failed", "error": str(e)}

    if success:
        get_images_collection().update_one(
            {"_id": ObjectId(image_id)},
            {"$set": {"cbir_indexed": True, "cbir_indexed_at": datetime.utcnow(), "cbir_id": result.get("id")},
             "$unset": {"cbir_error": "", "cbir_failed_at": ""}},
        )
        logger.info("Image %s indexed successfully", image_id)
        return {"status": "success", "cbir_id": result.get("id")}

    logger.error("Failed to index image %s: %s", image_id, message)
    _mark_index_failure([image_id], message)
    return {"status": "failed", "error": message}


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_index_batch")
def cbir_index_batch(
    self,
    user_id: str,
    image_items: list
):
    """
    Index multiple images in batch asynchronously.

    Used after PDF and panel extraction and by manual re-indexing. Retries with
    backoff while CBIR is unavailable; images that still cannot be indexed are
    marked (cbir_indexed=False, cbir_error) and kept.

    Args:
        user_id: User ID for multi-tenancy
        image_items: List of dicts with 'image_id', 'image_path', 'labels'
    """
    image_ids = [item.get("image_id") for item in image_items]
    logger.info("Batch indexing %s images for user %s", len(image_items), user_id)

    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        _retry_while_cbir_down(self, cbir_message)
        _mark_index_failure(image_ids, "Similarity search service unavailable")
        return {"status": "failed", "error": "CBIR service unavailable", "failed_image_ids": image_ids}

    try:
        indexed, failed, error = _index_chunk(user_id, image_items)
    except Exception as e:
        logger.error("Error batch indexing for user %s: %s", user_id, e)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=CBIR_RETRY_BASE_DELAY * (2 ** self.request.retries))
        indexed, failed, error = [], image_ids, f"Indexing error: {e}"

    _mark_indexed(indexed)
    _mark_index_failure(failed, error or "Indexing failed")
    status = "success" if not failed else ("partial" if indexed else "failed")
    logger.info("Batch indexed %s/%s images for user %s", len(indexed), len(image_items), user_id)
    return {"status": status, "indexed_count": len(indexed), "failed_image_ids": failed}


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

    Updates the indexing_jobs collection as chunks are processed so the
    frontend can poll for progress. Images that cannot be indexed are kept and
    marked; the job ends as completed, partial or failed.

    Args:
        job_id: Unique job ID for tracking (indexing_jobs collection)
        user_id: User ID for multi-tenancy
        image_items: List of dicts with 'image_id', 'image_path', 'labels'
        main_job_id: Optional job ID for the main jobs dashboard
    """
    jobs_col = get_indexing_jobs_collection()
    total_images = len(image_items)
    terminal_statuses = [
        IndexingJobStatus.COMPLETED.value,
        IndexingJobStatus.PARTIAL.value,
        IndexingJobStatus.FAILED.value
    ]

    def update_job(status: str, processed: int, indexed: int, failed: int, current_step: str,
                   errors: list = None, completed: bool = False):
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
        # Never overwrite a terminal state written by another task instance
        jobs_col.update_one({"_id": job_id, "status": {"$nin": terminal_statuses}}, {"$set": update_doc})

    existing_job = jobs_col.find_one({"_id": job_id})
    if existing_job and existing_job.get("status") in terminal_statuses:
        logger.warning("Job %s already in terminal state '%s', skipping", job_id, existing_job.get('status'))
        return {"job_id": job_id, "status": existing_job.get("status")}

    if main_job_id:
        update_main_job_progress(main_job_id, user_id, JobStatus.PROCESSING, 5, "Starting batch indexing...")

    image_ids = [item["image_id"] for item in image_items]
    cbir_healthy, cbir_message = check_cbir_health()
    if not cbir_healthy:
        update_job(IndexingJobStatus.PENDING.value, 0, 0, 0, "Waiting for the similarity search service...")
        _retry_while_cbir_down(self, cbir_message)
        message = NOT_INDEXED_MESSAGE.format(count=total_images)
        _mark_index_failure(image_ids, "Similarity search service unavailable")
        update_job(IndexingJobStatus.FAILED.value, total_images, 0, total_images, message, errors=[message], completed=True)
        if main_job_id:
            complete_job(main_job_id, user_id, JobStatus.FAILED, errors=[message])
        return {"status": IndexingJobStatus.FAILED.value, "indexed_count": 0, "failed_count": total_images}

    indexed_ids: List[str] = []
    failed_ids: List[str] = []
    for start in range(0, total_images, INDEXING_BATCH_CHUNK_SIZE):
        chunk = image_items[start:start + INDEXING_BATCH_CHUNK_SIZE]
        update_job(
            IndexingJobStatus.PROCESSING.value, start, len(indexed_ids), len(failed_ids),
            f"Encoding images {start + 1} to {start + len(chunk)} of {total_images}",
        )
        try:
            indexed, failed, error = _index_chunk(user_id, chunk)
        except Exception as e:
            logger.error("Chunk indexing error for job %s: %s", job_id, e)
            indexed, failed, error = [], [item["image_id"] for item in chunk], f"Indexing error: {e}"
        _mark_indexed(indexed)
        _mark_index_failure(failed, error or "Indexing failed")
        indexed_ids += indexed
        failed_ids += failed

    if not failed_ids:
        final_status, final_step, errors = IndexingJobStatus.COMPLETED.value, f"Successfully indexed {len(indexed_ids)} images", []
        main_status = JobStatus.COMPLETED
    else:
        final_status = IndexingJobStatus.PARTIAL.value if indexed_ids else IndexingJobStatus.FAILED.value
        final_step = NOT_INDEXED_MESSAGE.format(count=len(failed_ids))
        errors = [final_step]
        main_status = JobStatus.PARTIAL if indexed_ids else JobStatus.FAILED

    update_job(final_status, total_images, len(indexed_ids), len(failed_ids), final_step, errors=errors, completed=True)
    if main_job_id:
        complete_job(
            main_job_id, user_id, main_status,
            output_data={"indexed_count": len(indexed_ids), "image_count": total_images},
            errors=errors or None,
        )

    logger.info("Job %s finished: %s", job_id, final_step)
    return {
        "status": final_status,
        "indexed_count": len(indexed_ids),
        "failed_count": len(failed_ids),
        "failed_image_ids": failed_ids,
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
    job = TrackedJob(user_id, None, analysis_id)

    def work():
        success, message, results = search_similar_images(
            user_id=user_id,
            image_path=query_image_path,
            top_k=top_k,
            labels=labels
        )
        if not success:
            return False, message, None
        enriched_results = _enrich_search_results(user_id, results)
        return True, message, {
            "timestamp": datetime.utcnow(),
            "query_image_id": query_image_id,
            "top_k": top_k,
            "labels_filter": labels,
            "matches_count": len(enriched_results),
            "matches": enriched_results
        }

    return run_analysis(self, job, "Searching for similar images...", work)


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
        logger.info("Deleting image %s from CBIR index", image_id)
        
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
            logger.info("Image %s removed from CBIR index", image_id)
            return {"status": "success"}
        else:
            logger.error("Failed to delete image %s from CBIR: %s", image_id, message)
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error("Error deleting image %s from CBIR: %s", image_id, e)
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
        logger.info("Updating CBIR labels for image %s: %s", image_id, labels)
        
        success, message = update_image_labels(user_id, image_path, labels)
        
        if success:
            logger.info("CBIR labels updated for image %s", image_id)
            return {"status": "success", "labels": labels}
        else:
            logger.warning("CBIR label update for image %s: %s", image_id, message)
            return {"status": "skipped", "message": message}
            
    except Exception as e:
        logger.error("Error updating CBIR labels for image %s: %s", image_id, e)
        raise self.retry(exc=e, countdown=60)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.cbir_delete_user_data")
def cbir_delete_user_data(self, user_id: str):
    """
    Delete all CBIR data for a user asynchronously.
    
    Args:
        user_id: User ID whose data to delete
    """
    try:
        logger.info("Deleting all CBIR data for user %s", user_id)
        
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
            logger.info("All CBIR data deleted for user %s", user_id)
            return {"status": "success"}
        else:
            logger.error("Failed to delete CBIR data for user %s: %s", user_id, message)
            return {"status": "failed", "error": message}
            
    except Exception as e:
        logger.error("Error deleting CBIR data for user %s: %s", user_id, e)
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
