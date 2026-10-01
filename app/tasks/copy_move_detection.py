"""
Copy-Move Detection tasks for async processing.

Supports two detection methods:
- 'keypoint': Advanced keypoint-based detection (recommended for cross-image)
- 'dense': Block-based dense matching
"""
import logging
from datetime import datetime, timezone

from app.celery_config import celery_app
from app.config.settings import CELERY_MAX_RETRIES
from app.schemas import AnalysisType, JobType
from app.tasks.lifecycle import TrackedJob, run_analysis
from app.utils.file_storage import analysis_output_dir
from app.utils.docker_copy_move import run_copy_move_detection_with_docker

logger = logging.getLogger(__name__)

# Method constants for clear documentation
METHOD_KEYPOINT = "keypoint"
METHOD_DENSE = "dense"


def _result_document(method: str, dense_method: int, results: dict, descriptor: str = None) -> dict:
    data = {
        "method": method,
        "timestamp": datetime.now(timezone.utc),
        "matches_image": results.get("matches_image"),
        "clusters_image": results.get("clusters_image"),
    }
    if method == METHOD_DENSE:
        data["dense_method"] = dense_method
    elif descriptor:
        data["descriptor"] = descriptor
    return data


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.detect_copy_move")
def detect_copy_move(
    self,
    analysis_id: str,
    image_id: str,
    user_id: str,
    image_path: str,
    method: str = METHOD_KEYPOINT,
    dense_method: int = 2,
    job_id: str = None
):
    """
    Run copy-move detection on an image asynchronously.

    Args:
        analysis_id: MongoDB ID of the analysis document
        image_id: MongoDB ID of the image
        user_id: User ID
        image_path: Path to the image file
        method: Detection method ('keypoint' or 'dense')
        dense_method: Dense method variant (1-5), only used when method='dense'
        job_id: Pre-created job ID from the route (for pending state tracking)
    """
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.COPY_MOVE_SINGLE, f"Copy-Move Detection ({method})",
        {"image_id": image_id, "analysis_id": analysis_id, "method": method}, analysis_id=analysis_id,
    )

    def work():
        success, message, results = run_copy_move_detection_with_docker(
            analysis_id=analysis_id,
            analysis_type=AnalysisType.SINGLE_IMAGE_COPY_MOVE,
            user_id=user_id,
            image_path=image_path,
            method=method,
            dense_method=dense_method
        )
        return success, message, _result_document(method, dense_method, results) if success else None

    return run_analysis(self, job, "Running detection algorithm...", work,
                        output_dir=analysis_output_dir(user_id, analysis_id, AnalysisType.SINGLE_IMAGE_COPY_MOVE))


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.detect_copy_move_cross")
def detect_copy_move_cross(
    self,
    analysis_id: str,
    source_image_id: str,
    target_image_id: str,
    user_id: str,
    source_image_path: str,
    target_image_path: str,
    method: str = METHOD_KEYPOINT,
    dense_method: int = 2,
    descriptor: str = "cv_rsift",
    job_id: str = None
):
    """
    Run cross-image copy-move detection asynchronously.

    Args:
        analysis_id: MongoDB ID of the analysis document
        source_image_id: MongoDB ID of the source image
        target_image_id: MongoDB ID of the target image
        user_id: User ID
        source_image_path: Path to the source image file
        target_image_path: Path to the target image file
        method: Detection method ('keypoint' or 'dense')
        dense_method: Dense method variant (1-5), only used when method='dense'
        descriptor: Keypoint descriptor type, only used when method='keypoint'
        job_id: Pre-created job ID from the route (for pending state tracking)
    """
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.COPY_MOVE_CROSS, f"Cross Copy-Move Detection ({method})",
        {"source_image_id": source_image_id, "target_image_id": target_image_id,
         "analysis_id": analysis_id, "method": method},
        analysis_id=analysis_id,
    )

    def work():
        success, message, results = run_copy_move_detection_with_docker(
            analysis_id=analysis_id,
            analysis_type=AnalysisType.CROSS_IMAGE_COPY_MOVE,
            user_id=user_id,
            image_path=source_image_path,
            target_image_path=target_image_path,
            method=method,
            dense_method=dense_method,
            descriptor=descriptor
        )
        if not success:
            return False, message, None
        return True, message, _result_document(method, dense_method, results, descriptor)

    return run_analysis(self, job, "Running cross-image detection...", work,
                        output_dir=analysis_output_dir(user_id, analysis_id, AnalysisType.CROSS_IMAGE_COPY_MOVE))
