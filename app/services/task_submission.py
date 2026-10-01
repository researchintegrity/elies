"""
Queue Celery tasks on behalf of a user request (issue #68).

Routes create the job log (and, for analyses, the analysis record) before
queueing, so the dashboards show the job immediately. If queueing then
fails because the broker is down, both are marked failed instead of
staying "pending" forever, and the client gets HTTP 503.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId

from app.db.mongodb import get_analyses_collection
from app.exceptions import TransientError
from app.schemas import AnalysisStatus, JobStatus
from app.services.job_logger import attach_celery_task, complete_job

logger = logging.getLogger(__name__)

QUEUE_UNAVAILABLE = "The task queue is unavailable. Please try again in a few minutes."


def submit_task(task, task_kwargs: dict, *, owner_id: str, job_id: Optional[str] = None,
                analysis_id: Optional[str] = None):
    """
    ``task.delay(**task_kwargs)``, recording the Celery task id on the job.

    Raises:
        TransientError (HTTP 503): the broker could not be reached
    """
    try:
        result = task.delay(**task_kwargs)
    except Exception as e:
        logger.error("Could not queue %s for user %s: %s", task.name, owner_id, e)
        if job_id:
            complete_job(job_id, owner_id, JobStatus.FAILED, errors=["Task queue unavailable"])
        if analysis_id:
            get_analyses_collection().update_one(
                {"_id": ObjectId(analysis_id)},
                {"$set": {"status": AnalysisStatus.FAILED, "error": "Task queue unavailable",
                          "updated_at": datetime.now(timezone.utc)}},
            )
        raise TransientError(QUEUE_UNAVAILABLE) from e

    if job_id:
        attach_celery_task(job_id, result.id)
    return result
