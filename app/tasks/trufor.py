"""
TruFor Detection tasks for async processing
"""
from celery import current_task
from app.celery_config import celery_app
from app.db.mongodb import get_analyses_collection
from app.utils.docker_trufor import run_trufor_detection_with_docker
from app.config.settings import CELERY_MAX_RETRIES
from app.schemas import AnalysisStatus, JobType, JobStatus
from app.services.job_logger import create_job_log, update_job_progress, complete_job
from bson import ObjectId
from datetime import datetime
import logging

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
        job_id: Optional pre-created job ID from the route (for pending state tracking)
    """
    analyses_col = get_analyses_collection()
    
    def update_status(status_msg: str):
        """Callback to update analysis status in DB"""
        try:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status_message": status_msg,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            # Also update job progress
            if job_id:
                update_job_progress(job_id, user_id, None, None, status_msg)
        except Exception as e:
            logger.error(f"Failed to update status for analysis {analysis_id}: {e}")

    try:
        # Use provided job_id or create one if not provided (backward compatibility)
        if not job_id:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.TRUFOR,
                title="TruFor Forgery Detection",
                celery_task_id=self.request.id,
                input_data={"image_id": image_id, "analysis_id": analysis_id, "save_noiseprint": save_noiseprint}
            )
        
        # Update status to processing
        update_job_progress(job_id, user_id, JobStatus.PROCESSING, 10, "Starting TruFor detection...")
        analyses_col.update_one(
            {"_id": ObjectId(analysis_id)},
            {
                "$set": {
                    "status": AnalysisStatus.PROCESSING,
                    "status_message": "Starting TruFor detection...",
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        logger.info(f"Starting TruFor detection for analysis {analysis_id} (image {image_id}), save_noiseprint={save_noiseprint}")
        
        # Run detection
        success, message, results = run_trufor_detection_with_docker(
            analysis_id=analysis_id,
            user_id=user_id,
            image_path=image_path,
            save_noiseprint=save_noiseprint,
            status_callback=update_status
        )
        
        if success:
            # Build results dict, including noiseprint if available
            results_dict = {
                "timestamp": datetime.utcnow(),
                "pred_map": results.get('pred_map'),
                "conf_map": results.get('conf_map'),
                "files": results.get('files')
            }
            # Include noiseprint if it was saved
            if results.get('noiseprint'):
                results_dict["noiseprint"] = results.get('noiseprint')
            
            # Update with results
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.COMPLETED,
                        "status_message": "Completed",
                        "results": results_dict,
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.COMPLETED, {"analysis_id": analysis_id})
            logger.info(f"TruFor detection completed for analysis {analysis_id}")
            return {"status": "completed", "results": results}
        else:
            # Update with failure
            logger.error(f"TruFor detection failed for analysis {analysis_id}: {message}")
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": message,
                        "status_message": "Failed",
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            complete_job(job_id, user_id, JobStatus.FAILED, errors=[message])
            return {"status": "failed", "error": message}

    except Exception as e:
        logger.exception(f"Error in TruFor detection task for analysis {analysis_id}")
        try:
            analyses_col.update_one(
                {"_id": ObjectId(analysis_id)},
                {
                    "$set": {
                        "status": AnalysisStatus.FAILED,
                        "error": str(e),
                        "status_message": "System Error",
                        "updated_at": datetime.utcnow()
                    }
                }
            )
            if job_id:
                complete_job(job_id, user_id, JobStatus.FAILED, errors=[str(e)])
        except:
            pass
        raise self.retry(exc=e)
