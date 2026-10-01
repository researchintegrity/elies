"""
Job logging service for unified background job tracking.

Provides functions to create, update, and complete job log entries from
Celery tasks and routes, and publishes job events for the SSE stream
(/jobs/stream).

Events are published on Redis (channel ``elies:jobs:<user_id>``) so that
events emitted by Celery workers reach the API process that holds the SSE
connection (issue #64). If Redis is unreachable, events are delivered to
SSE subscribers in the current process only.
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis

from app.config.settings import JOB_EVENTS_REDIS_URL, JOB_RETENTION_DAYS, MAX_ACTIVE_JOBS_PER_USER
from app.db.mongodb import get_jobs_collection
from app.exceptions import TooManyJobsError
from app.request_context import current_request_id
from app.schemas import JobStatus, JobType

logger = logging.getLogger(__name__)

# ============================================================================
# PUB/SUB NOTIFICATION SYSTEM
# ============================================================================

JOB_EVENTS_CHANNEL_PREFIX = "elies:jobs:"
_REDIS_RETRY_AFTER = 30  # seconds to wait before trying Redis again after a failure

_redis_client: Optional[redis.Redis] = None
_redis_unavailable_until = 0.0

# Fallback in-process subscribers: user_id -> [(event loop, queue)]
_subscribers: Dict[str, List[Tuple[asyncio.AbstractEventLoop, "asyncio.Queue[Dict[str, Any]]"]]] = {}


def job_events_channel(user_id: str) -> str:
    return f"{JOB_EVENTS_CHANNEL_PREFIX}{user_id}"


def _get_redis() -> Optional[redis.Redis]:
    global _redis_client
    if time.monotonic() < _redis_unavailable_until:
        return None
    if _redis_client is None:
        _redis_client = redis.Redis.from_url(JOB_EVENTS_REDIS_URL, socket_connect_timeout=1, socket_timeout=2)
    return _redis_client


def _publish_to_redis(user_id: str, notification: dict) -> bool:
    global _redis_unavailable_until
    client = _get_redis()
    if client is None:
        return False
    try:
        client.publish(job_events_channel(user_id), json.dumps(notification, default=str))
        return True
    except redis.RedisError as e:
        logger.warning("Job events: Redis unavailable (%s); delivering in-process only", e)
        _redis_unavailable_until = time.monotonic() + _REDIS_RETRY_AFTER
        return False


def subscribe(user_id: str) -> "asyncio.Queue[Dict[str, Any]]":
    """
    Subscribe to in-process job notifications for a user (fallback path).
    Must be called from the event loop that will read the queue.
    """
    queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=100)
    _subscribers.setdefault(user_id, []).append((asyncio.get_running_loop(), queue))
    logger.debug("User %s subscribed to in-process job notifications", user_id)
    return queue


def unsubscribe(user_id: str, queue: asyncio.Queue) -> None:
    """Remove an in-process subscription."""
    entries = _subscribers.get(user_id, [])
    _subscribers[user_id] = [(loop, q) for loop, q in entries if q is not queue]
    if not _subscribers[user_id]:
        del _subscribers[user_id]


def _put_nowait(queue: asyncio.Queue, notification: dict) -> None:
    try:
        queue.put_nowait(notification)
    except asyncio.QueueFull:
        logger.warning("Job event queue full; dropping event %s", notification.get("event"))


def _deliver_locally(user_id: str, notification: dict) -> None:
    for loop, queue in list(_subscribers.get(user_id, [])):
        # Callers may run in a worker thread; asyncio queues are not thread-safe
        try:
            loop.call_soon_threadsafe(_put_nowait, queue, notification)
        except RuntimeError:
            pass  # the subscriber's loop has closed


def _notify_subscribers(user_id: str, notification: dict) -> None:
    """Publish a job event to every SSE stream of this user."""
    if not _publish_to_redis(user_id, notification):
        _deliver_locally(user_id, notification)


def ensure_job_capacity(user_id: str) -> None:
    """
    Refuse to queue another heavy job when the user already has
    MAX_ACTIVE_JOBS_PER_USER pending or processing (0 disables the limit).

    Raises:
        TooManyJobsError (HTTP 429)
    """
    if MAX_ACTIVE_JOBS_PER_USER <= 0:
        return
    active = get_jobs_collection().count_documents({
        "user_id": user_id,
        "status": {"$in": [JobStatus.PENDING.value, JobStatus.PROCESSING.value]},
        "job_type": {"$in": [t.value for t in HEAVY_JOB_TYPES]},
    })
    if active >= MAX_ACTIVE_JOBS_PER_USER:
        raise TooManyJobsError(
            f"You already have {active} jobs waiting or running. Please wait for some to finish."
        )


# Job types that run analysis tools (counted by ensure_job_capacity)
HEAVY_JOB_TYPES = (
    JobType.COPY_MOVE_SINGLE,
    JobType.COPY_MOVE_CROSS,
    JobType.TRUFOR,
    JobType.PANEL_EXTRACTION,
    JobType.IMAGE_EXTRACTION,
    JobType.WATERMARK_REMOVAL,
    JobType.PROVENANCE,
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
    job_id = f"job_{user_id}_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:8]}"
    jobs_col = get_jobs_collection()
    
    now = datetime.now(timezone.utc)
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
        # Correlates the job with the API request that started it (issue #77)
        "request_id": current_request_id(),
        # Reset on completion; set now so jobs abandoned by a dead worker expire too
        "expires_at": now + timedelta(days=JOB_RETENTION_DAYS)
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
    now = datetime.now(timezone.utc)
    
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
        job = jobs_col.find_one_and_update({"_id": job_id}, update, projection={"job_type": 1})
    except Exception as e:
        logger.error("Failed to update job progress for %s: %s", job_id, e)
        return

    # Notify subscribers
    _notify_subscribers(user_id, {
        "event": "job_progress",
        "job_id": job_id,
        "job_type": job.get("job_type") if job else None,
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
    now = datetime.now(timezone.utc)
    
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
        job = jobs_col.find_one_and_update({"_id": job_id}, update, projection={"job_type": 1})
        logger.info("Completed job: %s with status %s", job_id, status.value)
    except Exception as e:
        logger.error("Failed to complete job %s: %s", job_id, e)
        return

    # Notify subscribers
    event = {
        JobStatus.COMPLETED: "job_completed",
        JobStatus.PARTIAL: "job_completed",
    }.get(status, "job_failed")
    _notify_subscribers(user_id, {
        "event": event,
        "job_id": job_id,
        "job_type": job.get("job_type") if job else None,
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
