"""
Job monitoring routes for tracking background task progress.

Provides endpoints for:
- Listing jobs with pagination and filters
- Getting job statistics
- Getting individual job details
- SSE streaming for real-time job notifications
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from typing import AsyncIterator, Optional

import redis
import redis.asyncio as aioredis
import math
import asyncio
import json
import logging
import time

from app.config.settings import JOB_EVENTS_REDIS_URL
from app.utils.security import get_current_user
from app.db.mongodb import get_jobs_collection
from app.schemas import (
    JobLogResponse,
    JobListResponse,
    JobStatsResponse
)
from app.services.job_logger import job_events_channel, subscribe, unsubscribe

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

# Job types that are secondary/internal and should not appear in the dashboard
# These are automatically triggered by other operations (e.g., CBIR indexing after upload)
EXCLUDED_JOB_TYPES = ["cbir_index", "cbir_search", "cbir_delete"]


@router.get("/stats", response_model=JobStatsResponse)
def get_job_stats(current_user: dict = Depends(get_current_user)):
    """
    Get job statistics for the current user.
    
    Returns summary counts by status for dashboard header cards.
    """
    user_id = str(current_user["_id"])
    jobs_col = get_jobs_collection()
    
    # Base match filter excluding secondary CBIR jobs
    base_match = {"user_id": user_id, "job_type": {"$nin": EXCLUDED_JOB_TYPES}}
    
    # Aggregate counts by status
    status_pipeline = [
        {"$match": base_match},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}}
    ]
    status_counts = {doc["_id"]: doc["count"] for doc in jobs_col.aggregate(status_pipeline)}
    
    # Aggregate counts by type
    type_pipeline = [
        {"$match": base_match},
        {"$group": {"_id": "$job_type", "count": {"$sum": 1}}}
    ]
    type_counts = {doc["_id"]: doc["count"] for doc in jobs_col.aggregate(type_pipeline)}
    
    total = sum(status_counts.values())
    
    return JobStatsResponse(
        total_jobs=total,
        pending=status_counts.get("pending", 0),
        processing=status_counts.get("processing", 0),
        completed=status_counts.get("completed", 0),
        failed=status_counts.get("failed", 0),
        partial=status_counts.get("partial", 0),
        by_type=type_counts
    )


KEEPALIVE_SECONDS = 30
POLL_SECONDS = 1.0           # how often the stream checks both event sources
REDIS_RETRY_SECONDS = 5      # how often a stream without Redis checks whether it is back


def _async_redis_client():
    return aioredis.from_url(JOB_EVENTS_REDIS_URL, socket_connect_timeout=1)


async def _redis_reachable(client) -> bool:
    try:
        return bool(await client.ping())
    except (redis.RedisError, OSError):
        return False


def _sse(data: str) -> str:
    return f"data: {data}\n\n"


async def job_event_stream(user_id: str) -> AsyncIterator[str]:
    """
    Server-sent events for one user's jobs.

    Events arrive two ways: on the user's Redis channel (published by the API
    and by Celery workers) and on an in-process queue (events this API process
    could not publish because Redis was unreachable for it).

    A stream opened while Redis is unreachable uses the queue only, and closes
    as soon as Redis is back, so the client reconnects and receives worker
    events again; a stream that loses Redis also closes. The frontend
    reconnects automatically when a stream ends.
    """
    queue = subscribe(user_id)
    client = _async_redis_client()
    pubsub = client.pubsub()
    try:
        try:
            await pubsub.subscribe(job_events_channel(user_id))
            use_redis = True
        except (redis.RedisError, OSError) as e:
            logger.warning("Job stream: Redis unavailable (%s); using in-process events", e)
            use_redis = False

        last_sent = last_redis_check = time.monotonic()
        while True:
            if use_redis:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=POLL_SECONDS)
                except (redis.RedisError, OSError) as e:
                    logger.warning("Job stream: lost Redis (%s); closing so the client reconnects", e)
                    return
                if message and message.get("type") == "message":
                    data = message["data"]
                    yield _sse(data.decode() if isinstance(data, bytes) else data)
                    last_sent = time.monotonic()
            else:
                try:
                    notification = await asyncio.wait_for(queue.get(), timeout=POLL_SECONDS)
                    yield _sse(json.dumps(notification, default=str))
                    last_sent = time.monotonic()
                except asyncio.TimeoutError:
                    pass

            while not queue.empty():
                yield _sse(json.dumps(queue.get_nowait(), default=str))
                last_sent = time.monotonic()

            now = time.monotonic()
            if not use_redis and now - last_redis_check >= REDIS_RETRY_SECONDS:
                last_redis_check = now
                if await _redis_reachable(client):
                    logger.info("Job stream: Redis is back; closing so the client reconnects")
                    return
            if now - last_sent >= KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_sent = now
    finally:
        unsubscribe(user_id, queue)
        for owner, method in ((pubsub, "unsubscribe"), (pubsub, "aclose"), (client, "aclose")):
            try:
                await getattr(owner, method)()
            except Exception:  # noqa: BLE001 - best effort cleanup of a broken connection
                pass


@router.get("/stream")
async def stream_job_updates(current_user: dict = Depends(get_current_user)):
    """
    SSE endpoint for real-time job notifications.

    Events:
    - job_started: New job queued
    - job_progress: Job progress update
    - job_completed: Job finished successfully (or partially)
    - job_failed: Job failed with error
    """
    return StreamingResponse(
        job_event_stream(str(current_user["_id"])),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # Disable nginx buffering
        }
    )


@router.get("", response_model=JobListResponse)
def list_jobs(
    job_type: Optional[str] = Query(None, description="Filter by job type"),
    job_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: dict = Depends(get_current_user)
):
    """
    List jobs with pagination and filters.
    
    Returns a paginated list of all jobs for the current user.
    """
    user_id = str(current_user["_id"])
    jobs_col = get_jobs_collection()
    
    # Build query - exclude secondary CBIR jobs unless specifically requested
    query = {"user_id": user_id}
    if job_type:
        query["job_type"] = job_type
    else:
        # Exclude secondary job types that are triggered automatically
        query["job_type"] = {"$nin": EXCLUDED_JOB_TYPES}
    if job_status:
        query["status"] = job_status
    
    # Count total matching documents
    total = jobs_col.count_documents(query)
    total_pages = max(1, math.ceil(total / per_page))
    
    # Fetch paginated results sorted by created_at descending
    cursor = (
        jobs_col.find(query)
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    
    # Convert to response models
    items = []
    for doc in cursor:
        doc["job_id"] = doc.pop("_id")
        items.append(JobLogResponse(**doc))
    
    return JobListResponse(
        items=items,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1
    )


@router.get("/{job_id}", response_model=JobLogResponse)
def get_job(
    job_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a specific job by ID.
    
    Returns detailed information about a single job.
    """
    user_id = str(current_user["_id"])
    jobs_col = get_jobs_collection()
    
    job = jobs_col.find_one({"_id": job_id, "user_id": user_id})
    
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found"
        )
    
    job["job_id"] = job.pop("_id")
    return JobLogResponse(**job)
