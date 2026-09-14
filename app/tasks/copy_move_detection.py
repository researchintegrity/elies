"""
Copy-Move Detection tasks for async processing.

Supports two detection methods:
- 'keypoint': Advanced keypoint-based detection (recommended for cross-image)
- 'dense': Block-based dense matching
"""
from celery import current_task
from app.celery_config import celery_app
from app.db.mongodb import get_analyses_collection
from app.utils.docker_copy_move import run_copy_move_detection_with_docker
from app.config.settings import CELERY_MAX_RETRIES
from app.schemas import AnalysisStatus, AnalysisType, JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from bson import ObjectId
from datetime import datetime
import logging
import os

logger = logging.getLogger(__name__)

# Method constants for clear documentation
METHOD_KEYPOINT = "keypoint"
METHOD_DENSE = "dense"


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
        job_id: Optional pre-created job ID from the route (for pending state tracking)
    """
    analyses_col = get_analyses_collection()
    
    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.COPY_MOVE_SINGLE,
                title=f"Copy-Move Detection ({method})",
                celery_task_id=self.request.id,
                input_data={"image_id": image_id, "analysis_id": analysis_id, "method": method}
            )
        
        # Update status to processing
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Starting detection...")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.PROCESSING,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        method_desc = f"{method}" + (f" (variant {dense_method})" if method == METHOD_DENSE else "")
        logger.info(f"Starting copy-move detection for analysis {analysis_id} (image {image_id}) with method {method_desc}")
        
        update_job_progress(job_id, user_id, None, 30, "Running detection algorithm...")
        
        # Run detection
        success, message, results = run_copy_move_detection_with_docker(
            analysis_id=analysis_id,
            analysis_type=AnalysisType.SINGLE_IMAGE_COPY_MOVE,
            user_id=user_id,
            image_path=image_path,
            method=method,
            dense_method=dense_method
        )
        
        update_job_progress(job_id, user_id, None, 80, "Processing results...")
        
        if success:
            # Build result metadata
            result_data = {
                "method": method,
                "timestamp": datetime.utcnow(),
                "matches_image": results.get('matches_image'),
                "clusters_image": results.get('clusters_image')
            }
            # Include dense_method in results if applicable
            if method == METHOD_DENSE:
                result_data["dense_method"] = dense_method
            
            # Update with results
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.COMPLETED,
                        "results": result_data,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.COMPLETED, {"analysis_id": analysis_id})
            logger.info(f"Copy-move detection completed for analysis {analysis_id}")
            return {"status": "completed", "results": results}
        else:
            # Update with failure
            logger.error(f"Copy-move detection failed for analysis {analysis_id}: {message}")
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": message,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[message])
            return {"status": "failed", "error": message}

    except Exception as e:
        logger.exception(f"Error in copy-move detection task for analysis {analysis_id}")
        try:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": str(e),
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            if job_id:
                complete_job(job_id, user_id, JobStatus.FAILED, errors=[str(e)])
        except Exception as db_error:
            logger.error(f"Failed to update analysis status to failed: {db_error}")
        
        # Retry task if appropriate
        raise self.retry(exc=e)

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
        job_id: Optional pre-created job ID from the route (for pending state tracking)
    """
    analyses_col = get_analyses_collection()
    
    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.COPY_MOVE_CROSS,
                title=f"Cross-Image Copy-Move Detection ({method})",
                celery_task_id=self.request.id,
                input_data={
                    "source_image_id": source_image_id,
                    "target_image_id": target_image_id,
                    "analysis_id": analysis_id,
                    "method": method
                }
            )
        
        # Update status to processing
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Starting detection...")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.PROCESSING,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        if method == METHOD_KEYPOINT:
            method_desc = f"{method} (descriptor: {descriptor})"
        else:
            method_desc = f"{method} (variant {dense_method})"
        logger.info(
            f"Starting cross-image copy-move detection for analysis {analysis_id} "
            f"(source: {source_image_id}, target: {target_image_id}) with method {method_desc}"
        )
        
        update_job_progress(job_id, user_id, None, 30, "Running detection algorithm...")
        
        # Run detection
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
        
        update_job_progress(job_id, user_id, None, 80, "Processing results...")
        
        if success:
            # Build result metadata
            result_data = {
                "method": method,
                "timestamp": datetime.utcnow(),
                "matches_image": results.get('matches_image'),
                "clusters_image": results.get('clusters_image')
            }
            # Include method-specific parameters in results
            if method == METHOD_DENSE:
                result_data["dense_method"] = dense_method
            else:
                result_data["descriptor"] = descriptor
            
            # Update with results
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.COMPLETED,
                        "results": result_data,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.COMPLETED, {"analysis_id": analysis_id})
            logger.info(f"Cross-image copy-move detection completed for analysis {analysis_id}")
            return {"status": "completed", "results": results}
        else:
            # Update with failure
            logger.error(f"Cross-image copy-move detection failed for analysis {analysis_id}: {message}")
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": message,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[message])
            return {"status": "failed", "error": message}

    except Exception as e:
        logger.exception(f"Error in cross-image copy-move detection task for analysis {analysis_id}")
        try:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": str(e),
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            if job_id:
                complete_job(job_id, user_id, JobStatus.FAILED, errors=[str(e)])
        except Exception as db_error:
            logger.error(f"Failed to update analysis status to failed: {db_error}")
        
        # Retry task if appropriate
        raise self.retry(exc=e)
