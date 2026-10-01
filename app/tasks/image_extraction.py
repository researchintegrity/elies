"""
Image extraction tasks for async processing
"""
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from bson import ObjectId

from app.celery_config import celery_app
from app.config.settings import CELERY_MAX_RETRIES, convert_host_path_to_container
from app.db.mongodb import get_documents_collection, get_images_collection
from app.schemas import JobType
from app.services.deletion_service import delete_images, remove_file
from app.services.storage_service import track_writes
from app.tasks.cbir import cbir_index_batch
from app.tasks.lifecycle import TrackedJob, handle_task_exception
from app.utils.file_storage import figure_extraction_hook, get_extraction_output_path
from app.utils.metadata_parser import (
    extract_exif_metadata,
    is_pdf_extraction_filename,
    parse_pdf_extraction_filename,
)

logger = logging.getLogger(__name__)

FINISHED_STATUSES = ("completed", "completed_with_errors")


def _discard_previous_attempt(doc_id: str, user_id: str) -> None:
    """
    Make a retry start from a clean slate: drop images registered by an
    interrupted earlier attempt and any files left in the output directory
    (otherwise they would be registered a second time).
    """
    leftovers = list(get_images_collection().find({"document_id": doc_id, "user_id": user_id, "source_type": "extracted"}))
    if leftovers:
        logger.warning("Removing %d images from an earlier extraction attempt of %s", len(leftovers), doc_id)
        delete_images(leftovers)
    output_dir = get_extraction_output_path(user_id, doc_id)
    for leftover in output_dir.iterdir():
        if leftover.is_file():
            remove_file(str(leftover))


def _register_extracted_images(doc_id: str, user_id: str, extracted_files: List[dict]) -> Tuple[List[dict], List[str]]:
    """
    Create image records for extracted files, renaming each file to its id.

    Returns (registered_files, errors).
    """
    images_col = get_images_collection()
    registered, errors = [], []

    for image_file in extracted_files:
        original_filename = image_file["filename"]
        try:
            metadata = (
                parse_pdf_extraction_filename(original_filename)
                if is_pdf_extraction_filename(original_filename) else {}
            )
            image_id = ObjectId()
            old_path = Path(image_file["path"])
            new_filename = f"{image_id}{old_path.suffix.lower()}"
            new_path = old_path.with_name(new_filename)
            old_path.rename(new_path)
            stored_path = str(convert_host_path_to_container(new_path))

            images_col.insert_one({
                "_id": image_id,
                "user_id": user_id,
                "filename": new_filename,
                "file_path": stored_path,
                "file_size": image_file["size"],
                "source_type": "extracted",
                "document_id": doc_id,
                "pdf_page": metadata.get("page_number"),
                "page_bbox": metadata.get("bbox"),
                "extraction_mode": metadata.get("extraction_mode"),
                "original_filename": original_filename,
                "image_type": [],
                "uploaded_date": datetime.now(timezone.utc),
                "exif_metadata": extract_exif_metadata(stored_path),
            })
            registered.append({
                **image_file,
                "filename": new_filename,
                "path": stored_path,
                "mongodb_id": str(image_id),
            })
        except OSError as e:
            logger.error("Could not register extracted image %s: %s", original_filename, e, exc_info=True)
            errors.append(f"Could not store {original_filename}: {e}")

    return registered, errors


@celery_app.task(bind=True, max_retries=CELERY_MAX_RETRIES, name="tasks.extract_images")
def extract_images_from_document(self, doc_id: str, user_id: str, pdf_path: str, job_id: str = None):
    """
    Extract images from a PDF document asynchronously.

    - A PDF without images completes successfully with 0 images.
    - Only transient failures (Docker or MongoDB unreachable) are retried;
      a retry discards whatever an interrupted attempt registered.
    - A redelivered task for an already extracted document does nothing.

    Returns:
        Dict with doc_id, status, extracted_count and errors
    """
    documents_col = get_documents_collection()
    doc = documents_col.find_one({"_id": ObjectId(doc_id), "user_id": user_id})
    job = TrackedJob.ensure(
        self, user_id, job_id, JobType.IMAGE_EXTRACTION, "PDF Image Extraction", {"doc_id": doc_id}
    )

    if doc is None:
        job.fail("The document was deleted before extraction ran")
        return {"doc_id": doc_id, "status": "failed", "extracted_count": 0, "errors": ["Document not found"]}
    if doc.get("extraction_status") in FINISHED_STATUSES:
        logger.info("Document %s already extracted; skipping duplicate task", doc_id)
        return {"doc_id": doc_id, "status": doc["extraction_status"], "extracted_count": doc.get("extracted_image_count", 0)}

    def mark_document_failed(message: str) -> None:
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"extraction_status": "failed", "extraction_errors": [message],
                      "extraction_completed_at": datetime.now(timezone.utc)}},
        )

    try:
        job.start("Starting extraction...")
        documents_col.update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"extraction_status": "processing", "extraction_started_at": datetime.now(timezone.utc),
                      "extraction_retry_count": self.request.retries}},
        )
        _discard_previous_attempt(doc_id, user_id)

        job.progress(30, "Running PDF extraction...")
        with track_writes(user_id, get_extraction_output_path(user_id, doc_id)):
            extracted_count, extraction_errors, extracted_files = figure_extraction_hook(
                doc_id=doc_id, user_id=user_id, pdf_file_path=pdf_path
            )

        job.progress(60, "Processing extracted images...")
        registered, register_errors = _register_extracted_images(doc_id, user_id, extracted_files)
        errors = list(extraction_errors) + register_errors
    except BaseException as exc:  # noqa: BLE001 - re-raised by handle_task_exception
        handle_task_exception(self, exc, job, on_final_failure=mark_document_failed)

    if errors and not registered:
        extraction_status = "failed"
    elif errors:
        extraction_status = "completed_with_errors"
    else:
        extraction_status = "completed"

    documents_col.update_one(
        {"_id": ObjectId(doc_id)},
        {"$set": {
            "extraction_status": extraction_status,
            "extracted_image_count": len(registered),
            "extracted_images": registered,
            "extraction_errors": errors,
            "extraction_completed_at": datetime.now(timezone.utc),
        }},
    )

    if extraction_status == "failed":
        job.fail("; ".join(errors))
    else:
        job.complete(output_data={"doc_id": doc_id, "extracted_count": len(registered)},
                     partial_errors=errors or None)

    if registered:
        try:
            cbir_index_batch.delay(
                user_id=user_id,
                image_items=[{"image_id": f["mongodb_id"], "image_path": f["path"], "labels": []} for f in registered],
            )
        except Exception as e:
            logger.warning("Failed to queue CBIR indexing for doc_id=%s: %s", doc_id, e)

    logger.info("Extraction of %s finished: %s (%d images)", doc_id, extraction_status, len(registered))
    return {
        "doc_id": doc_id,
        "status": extraction_status,
        "extracted_count": len(registered),
        "errors": errors,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
