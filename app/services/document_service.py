"""
Document service for handling document operations.

Provides business logic for document CRUD operations.
"""
import logging

from app.db.mongodb import get_documents_collection
from app.schemas import JobStatus, JobType
from app.services.deletion_service import delete_document
from app.services.job_logger import complete_job, create_job_log
from app.services.resource_helpers import get_owned_resource
from app.utils.file_storage import update_user_storage_in_db

logger = logging.getLogger(__name__)


def delete_document_and_artifacts(
    document_id: str,
    user_id: str
) -> dict:
    """
    Delete a document with its PDF, extraction folder and extracted images
    (and everything derived from them).

    This is the single source of truth for document deletion logic.

    Raises:
        ValidationError: If document ID format is invalid.
        ResourceNotFoundError: If document not found.
    """
    doc = get_owned_resource(get_documents_collection, document_id, user_id, "Document")

    doc_name = doc.get("filename", document_id)
    job_id = create_job_log(
        user_id=user_id,
        job_type=JobType.DOCUMENT_DELETION,
        title=f"Deleting document: {doc_name}",
        input_data={"document_id": document_id, "filename": doc_name}
    )

    try:
        result = delete_document(doc)
        update_user_storage_in_db(user_id)
    except Exception as e:
        complete_job(job_id=job_id, user_id=user_id, status=JobStatus.FAILED, errors=[str(e)])
        raise

    complete_job(job_id=job_id, user_id=user_id, status=JobStatus.COMPLETED, output_data=result)
    return result
