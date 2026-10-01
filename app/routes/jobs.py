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

from app.config.settings import JOB_EVENTS_REDIS_URL
from app.utils.security import get_current_user
from app.db.mongodb import get_jobs_collection
from app.schemas import (
    JobType,
    JobStatus,
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


def _async_redis_client():
    return aioredis.from_url(JOB_EVENTS_REDIS_URL, socket_connect_timeout=1)


async def job_event_stream(user_id: str) -> AsyncIterator[str]:
    """
    Server-sent events for one user's jobs.

    Subscribes to the user's Redis channel, which receives events published
    by the API and by Celery workers. Falls back to in-process events when
    Redis is unreachable.
    """
    client = _async_redis_client()
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(job_events_channel(user_id))
    except (redis.RedisError, OSError) as e:
        logger.warning("Job stream: Redis unavailable (%s); using in-process events", e)
        await client.aclose()
        async for chunk in _local_event_stream(user_id):
            yield chunk
        return

    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS)
            if message and message.get("type") == "message":
                data = message["data"]
                yield f"data: {data.decode() if isinstance(data, bytes) else data}\n\n"
            else:
                yield ": keepalive\n\n"
    finally:
        await pubsub.unsubscribe()
        await pubsub.aclose()
        await client.aclose()


async def _local_event_stream(user_id: str) -> AsyncIterator[str]:
    queue = subscribe(user_id)
    try:
        while True:
            try:
                notification = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                yield f"data: {json.dumps(notification, default=str)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
    finally:
        unsubscribe(user_id, queue)


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
