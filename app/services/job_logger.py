"""
Job logging service for unified background job tracking.

Provides functions to create, update, and complete job log entries from Celery tasks.
Includes pub/sub notification system via in-memory queues for SSE streaming.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import uuid
import asyncio
import logging

from app.db.mongodb import get_jobs_collection
from app.schemas import JobType, JobStatus
from app.config.settings import JOB_RETENTION_DAYS

logger = logging.getLogger(__name__)

# ============================================================================
# PUB/SUB NOTIFICATION SYSTEM
# ============================================================================

# In-memory subscribers for SSE connections (per user_id)
# Each user_id maps to a list of asyncio.Queue objects
_subscribers: Dict[str, List["asyncio.Queue[Dict[str, Any]]"]] = {}


def subscribe(user_id: str) -> "asyncio.Queue[Dict[str, Any]]":
    """
    Subscribe to job notifications for a user.
    
    Args:
        user_id: User ID to subscribe for
        
    Returns:
        asyncio.Queue that will receive job notifications
    """
    if user_id not in _subscribers:
        _subscribers[user_id] = []

    # Note: queue.put_nowait() allows synchronous notification (e.g. from Celery tasks),
    # but concurrent access from other threads requires caution.
    queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=100)
    _subscribers[user_id].append(queue)
    logger.debug("User %s subscribed to job notifications", user_id)
    return queue


def unsubscribe(user_id: str, queue: asyncio.Queue) -> None:
    """
    Unsubscribe from job notifications.
    
    Args:
        user_id: User ID to unsubscribe
        queue: The queue to remove
    """
    if user_id in _subscribers and queue in _subscribers[user_id]:
        _subscribers[user_id].remove(queue)
        logger.debug("User %s unsubscribed from job notifications", user_id)
        # Clean up empty lists
        if not _subscribers[user_id]:
            del _subscribers[user_id]


def _notify_subscribers(user_id: str, notification: dict) -> None:
    """
    Push notification to all subscribers for a user.
    
    Args:
        user_id: User ID to notify
        notification: Notification payload to send
    """
    if user_id not in _subscribers:
        return
    
    for queue in _subscribers[user_id]:
        try:
            queue.put_nowait(notification)
        except asyncio.QueueFull:
            logger.warning(
                "Queue full for user %s, dropping notification (event=%s, job_id=%s)",
                user_id,
                notification.get("event"),
                notification.get("job_id"),
            )


# ============================================================================
# JOB LOGGING FUNCTIONS
# ============================================================================

def create_job_log(
    user_id: str,
    job_type: JobType,
    title: str,
    celery_task_id: Optional[str] = None,
    input_data: Optional[Dict[str, Any]] = None
) -> str:
    """
    Create a new job log entry.
    
    Args:
        user_id: User who initiated the job
        job_type: Type of background job
        title: Human-readable title for the job
        celery_task_id: Optional Celery task ID
        input_data: Optional input parameters for the job
        
    Returns:
        job_id: Unique identifier for the created job
    """
    job_id = f"job_{user_id}_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:8]}"
    jobs_col = get_jobs_collection()
    
    now = datetime.utcnow()
    job_doc = {
        "_id": job_id,
        "user_id": user_id,
        "job_type": job_type.value,
        "celery_task_id": celery_task_id,
        "status": JobStatus.PENDING.value,
        "title": title,
        "progress_percent": 0.0,
        "current_step": "Queued",
        "input_data": input_data,
        "output_data": None,
        "errors": [],
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "expires_at": None  # Set on completion
    }
    
    try:
        jobs_col.insert_one(job_doc)
        logger.info("Created job log: %s (%s) for user %s", job_id, job_type.value, user_id)
    except Exception as e:
        logger.error("Failed to create job log: %s", e)
        raise
    
    # Notify subscribers
    _notify_subscribers(user_id, {
        "event": "job_started",
        "job_id": job_id,
        "job_type": job_type.value,
        "status": JobStatus.PENDING.value,
        "title": title
    })
    
    return job_id


def update_job_progress(
    job_id: str,
    user_id: str,
    status: Optional[JobStatus] = None,
    progress_percent: Optional[float] = None,
    current_step: Optional[str] = None
) -> None:
    """
    Update job progress.
    
    Args:
        job_id: Job identifier
        user_id: User ID (for notifications)
        status: Optional new status
        progress_percent: Optional progress (0-100)
        current_step: Optional step description
    """
    jobs_col = get_jobs_collection()
    now = datetime.utcnow()
    
    update: Dict[str, Any] = {"$set": {"updated_at": now}}
    
    if status is not None:
        update["$set"]["status"] = status.value
        # Set started_at when transitioning to PROCESSING
        if status == JobStatus.PROCESSING:
            update["$set"]["started_at"] = now
    
    if progress_percent is not None:
        update["$set"]["progress_percent"] = min(100.0, max(0.0, progress_percent))
    
    if current_step is not None:
        update["$set"]["current_step"] = current_step
    
    try:
        jobs_col.update_one({"_id": job_id}, update)
    except Exception as e:
        logger.error("Failed to update job progress for %s: %s", job_id, e)
        return
    
    # Notify subscribers
    _notify_subscribers(user_id, {
        "event": "job_progress",
        "job_id": job_id,
        "job_type": None,  # Not included in progress updates for efficiency
        "status": status.value if status else None,
        "progress_percent": progress_percent,
        "current_step": current_step
    })


def complete_job(
    job_id: str,
    user_id: str,
    status: JobStatus,
    output_data: Optional[Dict[str, Any]] = None,
    errors: Optional[List[str]] = None,
    retention_days: Optional[int] = None
) -> None:
    """
    Mark a job as completed (success or failure).
    
    Args:
        job_id: Job identifier
        user_id: User ID (for notifications)
        status: Final status (COMPLETED, FAILED, or PARTIAL)
        output_data: Optional result summary
        errors: Optional list of error messages
        retention_days: Days until expiration (defaults to JOB_RETENTION_DAYS)
    """
    if retention_days is None:
        retention_days = JOB_RETENTION_DAYS
    
    jobs_col = get_jobs_collection()
    now = datetime.utcnow()
    
    update: Dict[str, Any] = {
        "$set": {
            "status": status.value,
            "completed_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(days=retention_days)
        }
    }
    
    # Set progress to 100% only on success
    if status == JobStatus.COMPLETED:
        update["$set"]["progress_percent"] = 100.0
        update["$set"]["current_step"] = "Completed"
    elif status == JobStatus.FAILED:
        update["$set"]["current_step"] = "Failed"
    elif status == JobStatus.PARTIAL:
        update["$set"]["current_step"] = "Partially completed"
    
    if output_data is not None:
        update["$set"]["output_data"] = output_data
    
    if errors is not None:
        update["$set"]["errors"] = errors
    
    try:
        jobs_col.update_one({"_id": job_id}, update)
        logger.info("Completed job: %s with status %s", job_id, status.value)
    except Exception as e:
        logger.error("Failed to complete job %s: %s", job_id, e)
        return
    
    # Notify subscribers
    event = "job_completed" if status == JobStatus.COMPLETED else "job_failed"
    _notify_subscribers(user_id, {
        "event": event,
        "job_id": job_id,
        "job_type": None,
        "status": status.value,
        "error": errors[0] if errors else None
    })


def attach_celery_task(job_id: str, celery_task_id: str) -> None:
    """Record which Celery task runs a job (used for ownership checks on task ids)."""
    try:
        get_jobs_collection().update_one({"_id": job_id}, {"$set": {"celery_task_id": celery_task_id}})
    except Exception as e:
        logger.error("Failed to attach Celery task %s to job %s: %s", celery_task_id, job_id, e)


def find_job_by_celery_task(celery_task_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """The job run by a Celery task, only if it belongs to ``user_id``."""
    return get_jobs_collection().find_one({"celery_task_id": celery_task_id, "user_id": user_id})


def get_job(job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a job by ID.
    
    Args:
        job_id: Job identifier
        user_id: User ID (for authorization)
        
    Returns:
        Job document or None if not found
    """
    jobs_col = get_jobs_collection()
    return jobs_col.find_one({"_id": job_id, "user_id": user_id})
