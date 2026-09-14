"""
Panel extraction service layer
"""
import logging
from typing import Dict, List, Any, Optional
from bson import ObjectId
from app.db.mongodb import get_images_collection
from app.schemas import JobType
from app.services.job_logger import create_job_log
from app.tasks.panel_extraction import extract_panels_from_images
logger = logging.getLogger(__name__)


def initiate_panel_extraction(
    image_ids: List[str],
    user_id: str
) -> Dict[str, Any]:
    """Initiate panel extraction for selected images.

    Validates that all images exist and belong to the user, then queues
    a Celery task to extract panels from them.

    Args:
        image_ids: List of MongoDB image document IDs
        user_id: User who owns the images

    Returns:
        Dict with task initiation result {
            task_id, status, image_ids, message
        }

    Raises:
        ValueError: If validation fails
    """
    images_col = get_images_collection()

    # Validate images
    image_paths = []
    validated_ids = []

    for img_id in image_ids:
        try:
            # Try to validate as a direct ObjectId first
            image_doc = None
            try:
                image_doc = images_col.find_one(
                    {"_id": ObjectId(img_id), "user_id": user_id}
                )
            except:
                # If img_id is not a valid ObjectId, try looking it up by filename
                # Format might be: docid-idx-filename
                pass
            
            # If not found by ID, try parsing the synthetic ID format (docid-idx-filename)
            if not image_doc and "-" in img_id:
                try:
                    # Extract filename from synthetic ID
                    # Format: docid-idx-filename or docid-idx-rest-of-filename
                    parts = img_id.split("-", 2)
                    if len(parts) >= 3:
                        filename = parts[2]  # Everything after the second dash
                        # Look up by filename and user
                        image_doc = images_col.find_one(
                            {"filename": filename, "user_id": user_id}
                        )
                        if image_doc:
                            # Update the ID to the actual MongoDB ID
                            img_id = str(image_doc["_id"])
                except:
                    pass

            if not image_doc:
                raise ValueError(f"Image not found or does not belong to user: {img_id}")

            # Only extracted and uploaded images can be used as source
            if image_doc.get("source_type") not in ["extracted", "uploaded"]:
                raise ValueError(
                    f"Cannot extract panels from {image_doc.get('source_type')} type image"
                )

            # Verify file exists
            file_path = image_doc.get("file_path")
            if not file_path:
                raise ValueError(f"Image document has no file_path: {img_id}")

            image_paths.append(file_path)
            validated_ids.append(img_id)

            logger.debug(f"Validated image {img_id}: {file_path}")
        except Exception as e:
            error_msg = f"Error validating image {img_id}: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg)

    if not validated_ids:
        raise ValueError("No valid images to process")

    # Create job log entry for the jobs dashboard (pending state)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.PANEL_EXTRACTION,
        title=f"Panel Extraction ({len(validated_ids)} images)",
        input_data={"image_ids": validated_ids, "image_count": len(validated_ids)}
    )

    # Queue Celery task
    try:
        task = extract_panels_from_images.delay(
            image_ids=validated_ids,
            user_id=user_id,
            image_paths=image_paths,
            job_id=job_id
        )

        result = {
            "task_id": task.id,
            "status": "queued",
            "image_ids": validated_ids,
            "message": f"Panel extraction queued for {len(validated_ids)} image(s)"
        }

        logger.info(f"Panel extraction task queued: {task.id} for user {user_id}")
        return result

    except Exception as e:
        error_msg = f"Error queuing panel extraction task: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise ValueError(error_msg)


def get_panel_extraction_status(
    task_id: str,
    user_id: str
) -> Dict[str, Any]:
    """Get the status of a panel extraction task.

    Returns current task status and results if completed.

    Args:
        task_id: Celery task ID
        user_id: User who initiated the extraction

    Returns:
        Dict with task status {
            task_id, status, image_ids, extracted_panels_count,
            extracted_panels (if completed), error (if failed)
        }
    """
    from celery.result import AsyncResult

    try:
        task_result = AsyncResult(task_id, app=extract_panels_from_images.app)

        # Get basic task info
        task_state = task_result.state
        task_info = task_result.info or {}

        # Validate user owns this task by checking if returned image_ids
        # Actually this is difficult without storing task metadata
        # For now, we just return the status
        # In production, you might store task metadata in a separate collection

        response = {
            "task_id": task_id,
            "status": _normalize_task_state(task_state),
            "image_ids": task_info.get("image_ids", []),
            "extracted_panels_count": task_info.get("extracted_panels_count", 0),
            "message": task_info.get("message"),
            "error": task_info.get("error")
        }

        # If task is completed, retrieve and include extracted panel documents
        if response["status"] == "completed":
            result_panel_ids = task_info.get("result_panel_ids", [])

            if result_panel_ids:
                try:
                    images_col = get_images_collection()
                    extracted_panels = []

                    for panel_id in result_panel_ids:
                        panel_doc = images_col.find_one(
                            {"_id": ObjectId(panel_id), "user_id": user_id}
                        )

                        if panel_doc:
                            # Convert to response format
                            panel_response = _convert_document_to_response(panel_doc)
                            extracted_panels.append(panel_response)

                    response["extracted_panels"] = extracted_panels

                except Exception as e:
                    logger.error(f"Error retrieving extracted panels: {str(e)}")
                    response["error"] = f"Retrieved panels but with errors: {str(e)}"

        logger.debug(f"Panel extraction status for task {task_id}: {response['status']}")
        return response

    except Exception as e:
        error_msg = f"Error getting panel extraction status: {str(e)}"
        logger.error(error_msg, exc_info=True)

        return {
            "task_id": task_id,
            "status": "error",
            "message": "Error retrieving task status",
            "error": error_msg
        }


def get_panels_by_source_image(
    source_image_id: str,
    user_id: str
) -> List[Dict[str, Any]]:
    """Get all panels extracted from a specific source image.

    Args:
        source_image_id: MongoDB ID of the source image
        user_id: User who owns the panels

    Returns:
        List of panel documents converted to response format
    """
    try:
        images_col = get_images_collection()

        # Query all panels with this source image
        panels = images_col.find({
            "source_image_id": source_image_id,
            "source_type": "panel",
            "user_id": user_id
        })

        result = []
        for panel_doc in panels:
            panel_response = _convert_document_to_response(panel_doc)
            result.append(panel_response)

        logger.debug(f"Found {len(result)} panels for source image {source_image_id}")
        return result

    except Exception as e:
        error_msg = f"Error retrieving panels for source image: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise ValueError(error_msg)


def _normalize_task_state(state: str) -> str:
    """Normalize Celery task state to our standard states.

    Args:
        state: Celery task state (PENDING, STARTED, SUCCESS, FAILURE, etc.)

    Returns:
        Normalized state (queued, processing, completed, failed, error)
    """
    state_mapping = {
        "PENDING": "queued",
        "STARTED": "processing",
        "SUCCESS": "completed",
        "FAILURE": "failed",
        "RETRY": "queued",
        "REVOKED": "failed"
    }

    return state_mapping.get(state, "unknown")


def _convert_document_to_response(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MongoDB document to response format.

    Args:
        doc: MongoDB document

    Returns:
        Dictionary formatted for API response with _id field (for Pydantic alias)
    """
    return {
        "_id": str(doc.get("_id")),
        "user_id": doc.get("user_id"),
        "filename": doc.get("filename"),
        "file_path": doc.get("file_path"),
        "file_size": doc.get("file_size"),
        "source_type": doc.get("source_type"),
        "document_id": doc.get("document_id"),
        "source_image_id": doc.get("source_image_id"),
        "panel_id": doc.get("panel_id"),
        "panel_type": doc.get("panel_type"),
        "bbox": doc.get("bbox"),
        "uploaded_date": doc.get("uploaded_date"),
        "user_storage_used": 0,
        "user_storage_remaining": 1073741824
    }
