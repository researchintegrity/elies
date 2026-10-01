"""
TruFor Detection tasks for async processing
"""
import logging
from datetime import datetime

from app.celery_config import celery_app
from app.config.settings import CELERY_MAX_RETRIES
from app.schemas import AnalysisType, JobType
from app.tasks.lifecycle import TrackedJob, run_analysis
from app.utils.file_storage import analysis_output_dir
from app.utils.docker_trufor import run_trufor_detection_with_docker

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.detect_trufor")
def detect_trufor(
    self,
    analysis_id: str,
    image_id: str,
    user_id: str,
    image_path: str,
    save_noiseprint: bool = False,
    job_id: str = None
):
    """
    Run TruFor detection on an image asynchronously.

    Args:
        analysis_id: MongoDB ID of the analysis document
        image_id: MongoDB ID of the image
        user_id: User ID
        image_path: Path to the image file
        save_noiseprint: Whether to save the noiseprint map (default: False)
        job_id: Pre-created job ID from the route (for pending state tracking)
    """
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.TRUFOR, "TruFor Forgery Detection",
        {"image_id": image_id, "analysis_id": analysis_id, "save_noiseprint": save_noiseprint},
        analysis_id=analysis_id,
    )

    def report_status(message: str) -> None:
        try:
            job.progress(None, message)
        except Exception as e:
            logger.error("Failed to update status for analysis %s: %s", analysis_id, e)

    def work():
        success, message, results = run_trufor_detection_with_docker(
            analysis_id=analysis_id,
            user_id=user_id,
            image_path=image_path,
            save_noiseprint=save_noiseprint,
            status_callback=report_status
        )
        if not success:
            return False, message, None
        stored = {
            "timestamp": datetime.utcnow(),
            "pred_map": results.get("pred_map"),
            "conf_map": results.get("conf_map"),
            "files": results.get("files"),
        }
        if results.get("noiseprint"):
            stored["noiseprint"] = results["noiseprint"]
        return True, message, stored

    return run_analysis(self, job, "Starting TruFor detection...", work,
                        output_dir=analysis_output_dir(user_id, analysis_id, AnalysisType.TRUFOR))
