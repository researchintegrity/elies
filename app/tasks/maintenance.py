"""
Maintenance tasks: account deletion, reaping jobs abandoned by dead workers
and reconciling storage usage with the disk.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from app.celery_config import celery_app
from app.config.settings import STALE_PENDING_HOURS, STALE_PROCESSING_MINUTES
from app.db.mongodb import (
    get_analyses_collection,
    get_documents_collection,
    get_indexing_jobs_collection,
    get_jobs_collection,
)
from app.schemas import JobStatus
from app.services.deletion_service import delete_user_account
from app.services.job_logger import complete_job
from app.services.storage_service import reconcile_all_storage

logger = logging.getLogger(__name__)

STALE_MESSAGE = "The job stopped responding (its worker was restarted or crashed). Please run it again."


@celery_app.task(bind=True, max_retries=3, name="tasks.delete_user_account")
def delete_user_account_task(self, user_id: str) -> dict:
    """Delete every record and file of a user (idempotent, safe to retry)."""
    try:
        return delete_user_account(user_id)
    except Exception as e:
        logger.error("Account deletion for %s failed: %s", user_id, e, exc_info=True)
        raise self.retry(exc=e, countdown=60)


def reap_stale_jobs(now: Optional[datetime] = None) -> Dict[str, int]:
    """
    Mark work that no worker is doing any more as failed (issue #68).

    Running work not updated for STALE_PROCESSING_MINUTES (well past the
    Celery hard time limit) and queued work older than STALE_PENDING_HOURS
    can only belong to a worker that died, e.g. out of memory or redeployed.
    """
    now = now or datetime.now(timezone.utc)
    processing_cutoff = now - timedelta(minutes=STALE_PROCESSING_MINUTES)
    pending_cutoff = now - timedelta(hours=STALE_PENDING_HOURS)
    stale = {"$or": [
        {"status": "processing", "updated_at": {"$lt": processing_cutoff}},
        {"status": "pending", "updated_at": {"$lt": pending_cutoff}},
    ]}
    counts = {"jobs": 0}

    for job in get_jobs_collection().find(stale, {"_id": 1, "user_id": 1}):
        complete_job(job["_id"], job["user_id"], JobStatus.FAILED, errors=[STALE_MESSAGE])
        counts["jobs"] += 1

    counts["analyses"] = get_analyses_collection().update_many(
        stale, {"$set": {"status": "failed", "error": STALE_MESSAGE, "updated_at": now}}
    ).modified_count

    counts["indexing_jobs"] = get_indexing_jobs_collection().update_many(
        stale, {"$set": {"status": "failed", "current_step": STALE_MESSAGE, "errors": [STALE_MESSAGE],
                         "updated_at": now, "completed_at": now}}
    ).modified_count

    counts["documents"] = get_documents_collection().update_many(
        {"$or": [
            {"extraction_status": "processing", "extraction_started_at": {"$lt": processing_cutoff}},
            {"extraction_status": "pending", "uploaded_date": {"$lt": pending_cutoff}},
        ]},
        {"$set": {"extraction_status": "failed", "extraction_errors": [STALE_MESSAGE]}},
    ).modified_count

    if any(counts.values()):
        logger.warning("Reaped stale work: %s", counts)
    return counts


@celery_app.task(name="tasks.reap_stale_jobs")
def reap_stale_jobs_task() -> Dict[str, int]:
    """Periodic task (Celery beat) wrapping reap_stale_jobs."""
    return reap_stale_jobs()


@celery_app.task(name="tasks.reconcile_storage")
def reconcile_storage_task() -> Dict[str, int]:
    """
    Periodic task (Celery beat): recompute every user's storage_used_bytes
    from their workspace, correcting drift in the running totals (#71).
    """
    return reconcile_all_storage()
