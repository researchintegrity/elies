"""
Shared lifecycle handling for Celery tasks (issue #68).

- Only transient infrastructure errors (Docker daemon, MongoDB or a
  microservice unreachable) are retried, with exponential backoff.
- A job or analysis is marked failed only once no retry will follow, so
  dashboards never show "failed" while a retry is pending.
- Hitting the Celery soft time limit fails the task without retrying.
- Any other exception is a bug or a permanent failure: it fails the task.
"""
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from bson import ObjectId
from celery.exceptions import SoftTimeLimitExceeded
from pymongo.errors import AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError
from requests import ConnectionError as RequestsConnectionError
from requests import Timeout as RequestsTimeout

from app.db.mongodb import get_analyses_collection
from app.exceptions import TransientError
from app.schemas import AnalysisStatus, JobStatus, JobType
from app.services.job_logger import complete_job, create_job_log, update_job_progress
from app.services.storage_service import track_writes

logger = logging.getLogger(__name__)

TRANSIENT_ERRORS = (
    TransientError,
    AutoReconnect,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    RequestsConnectionError,
    RequestsTimeout,
)
RETRY_BASE_DELAY = 30
RETRY_MAX_DELAY = 600


def retry_delay(retries: int) -> int:
    """Exponential backoff: 30s, 60s, 120s ... capped at 10 minutes."""
    return min(RETRY_BASE_DELAY * (2 ** retries), RETRY_MAX_DELAY)


class TrackedJob:
    """
    A background job as seen by users: the jobs dashboard entry and,
    optionally, the analysis record it produces.
    """

    def __init__(self, user_id: str, job_id: Optional[str], analysis_id: Optional[str] = None):
        self.user_id = user_id
        self.job_id = job_id
        self.analysis_id = analysis_id

    @classmethod
    def ensure(cls, task, user_id: str, job_id: Optional[str], job_type: JobType, title: str,
               input_data: Dict[str, Any], analysis_id: Optional[str] = None) -> "TrackedJob":
        """Use the job created by the route, or create one (older queued messages)."""
        if not job_id:
            job_id = create_job_log(
                user_id=user_id, job_type=job_type, title=title,
                celery_task_id=task.request.id, input_data=input_data,
            )
        return cls(user_id, job_id, analysis_id)

    def _update_analysis(self, fields: Dict[str, Any]) -> None:
        if self.analysis_id:
            fields["updated_at"] = datetime.now(timezone.utc)
            get_analyses_collection().update_one({"_id": ObjectId(self.analysis_id)}, {"$set": fields})

    def start(self, message: str) -> None:
        if self.job_id:
            update_job_progress(self.job_id, self.user_id, JobStatus.PROCESSING, 10, message)
        self._update_analysis({"status": AnalysisStatus.PROCESSING, "status_message": message})

    def progress(self, percent: Optional[float], message: str) -> None:
        if self.job_id:
            update_job_progress(self.job_id, self.user_id, None, percent, message)
        self._update_analysis({"status_message": message})

    def complete(self, results: Optional[Dict[str, Any]] = None, output_data: Optional[Dict[str, Any]] = None,
                 partial_errors: Optional[list] = None) -> None:
        fields: Dict[str, Any] = {"status": AnalysisStatus.COMPLETED, "status_message": "Completed"}
        if results is not None:
            fields["results"] = results
        self._update_analysis(fields)
        output: Dict[str, Any] = {"analysis_id": self.analysis_id} if self.analysis_id else {}
        output.update(output_data or {})
        status = JobStatus.PARTIAL if partial_errors else JobStatus.COMPLETED
        if self.job_id:
            complete_job(self.job_id, self.user_id, status, output_data=output or None, errors=partial_errors)

    def fail(self, message: str) -> None:
        self._update_analysis({"status": AnalysisStatus.FAILED, "status_message": "Failed", "error": message})
        if self.job_id:
            complete_job(self.job_id, self.user_id, JobStatus.FAILED, errors=[message])


def handle_task_exception(task, exc: BaseException, job: TrackedJob,
                          on_final_failure: Optional[Callable[[str], None]] = None) -> None:
    """
    Decide what an exception means for a task. Always raises: either
    ``task.retry(...)`` for transient errors with retries left, or the
    original exception after the job has been marked failed.
    """
    def fail(message: str) -> None:
        job.fail(message)
        if on_final_failure:
            on_final_failure(message)

    if isinstance(exc, SoftTimeLimitExceeded):
        fail("The task exceeded its time limit")
        raise exc

    if isinstance(exc, TRANSIENT_ERRORS):
        if task.request.retries < task.max_retries:
            countdown = retry_delay(task.request.retries)
            logger.warning("Transient error in %s (%s); retrying in %ss", task.name, exc, countdown)
            job.progress(None, f"Temporarily unavailable, retrying in {countdown}s")
            raise task.retry(exc=exc, countdown=countdown)
        fail(f"A required service is unavailable: {exc}")
        raise exc

    logger.exception("Task %s failed", task.name)
    fail(f"Unexpected error: {exc}")
    raise exc


def run_analysis(task, job: TrackedJob, start_message: str,
                 work: Callable[[], Tuple[bool, str, Optional[Dict[str, Any]]]],
                 output_data: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
                 output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Run ``work`` for an analysis task: ``work`` returns (success, message,
    results). Results are stored on the analysis on success; the failure
    message otherwise. Exceptions go through handle_task_exception.

    Files written to ``output_dir`` count against the user's storage quota.
    """
    try:
        job.start(start_message)
        with track_writes(job.user_id, output_dir) if output_dir else nullcontext():
            success, message, results = work()
    except BaseException as exc:  # noqa: BLE001 - re-raised by handle_task_exception
        handle_task_exception(task, exc, job)

    if success:
        job.complete(results, output_data(results or {}) if output_data else None)
        return {"status": "completed", "results": results}
    job.fail(message)
    return {"status": "failed", "error": message}
