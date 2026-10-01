"""
Cascading deletion of images, documents, analyses and whole accounts.

Rules (issue #65):
- Database records are removed first, then files on a best-effort basis.
- A file that is already missing counts as deleted, so deletion is idempotent
  and a record whose file vanished can still be removed.
- Everything derived from a resource goes with it: panels cropped from an
  image, its thumbnail, annotations, relationships, analyses (with their result
  folders) and its CBIR vectors.
- Files are only deleted inside the workspace (UPLOAD_DIR).
- The bytes freed are released from the owner's storage counter (issue #71).
"""
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from bson import ObjectId

from app.config.settings import UPLOAD_DIR
from app.db.mongodb import (
    get_analyses_collection,
    get_documents_collection,
    get_dual_annotations_collection,
    get_images_collection,
    get_indexing_jobs_collection,
    get_jobs_collection,
    get_relationships_collection,
    get_single_annotations_collection,
    get_users_collection,
)
from app.services.storage_service import path_size, release_storage, workspace_owner
from app.utils.file_storage import analysis_output_dir

logger = logging.getLogger(__name__)


def _within_workspace(path: Path) -> bool:
    try:
        return path.resolve().is_relative_to(UPLOAD_DIR.resolve())
    except (OSError, ValueError):
        return False


class FreedSpace:
    """Bytes freed per user during one deletion, released in one update each."""

    def __init__(self):
        self.by_user: Dict[str, int] = defaultdict(int)

    def add(self, path: Path, size: int) -> None:
        owner = workspace_owner(path)
        if owner and size:
            self.by_user[owner] += size

    def release(self) -> None:
        for user_id, size in self.by_user.items():
            release_storage(user_id, size)
        self.by_user.clear()


def remove_file(path: Optional[str], freed: Optional[FreedSpace] = None) -> int:
    """
    Delete a workspace file; missing files and paths outside the workspace are
    skipped. The freed bytes are released from the owner's storage counter, or
    recorded in ``freed`` for the caller to release. Returns the bytes freed.
    """
    if not path:
        return 0
    file_path = Path(path)
    if not _within_workspace(file_path):
        logger.warning("Not deleting file outside the workspace: %s", file_path)
        return 0
    size = path_size(file_path)
    try:
        file_path.unlink(missing_ok=True)
    except OSError as e:
        logger.warning("Could not delete file %s: %s", file_path, e)
        return 0
    _record_freed(file_path, size, freed)
    return size


def remove_tree(path: Path, freed: Optional[FreedSpace] = None) -> int:
    """Delete a workspace directory tree (see remove_file). Returns the bytes freed."""
    if not _within_workspace(path) or path.resolve() == UPLOAD_DIR.resolve():
        logger.warning("Not deleting directory outside the workspace: %s", path)
        return 0
    size = path_size(path)
    shutil.rmtree(path, ignore_errors=True)
    size -= path_size(path)  # whatever could not be deleted
    _record_freed(path, size, freed)
    return size


def _record_freed(path: Path, size: int, freed: Optional[FreedSpace]) -> None:
    if freed is not None:
        freed.add(path, size)
    else:
        owner = workspace_owner(path)
        if owner:
            release_storage(owner, size)


def _remove_dir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass  # not empty or already gone


def thumbnail_path(user_id: str, image_id: str) -> Path:
    return UPLOAD_DIR / user_id / "images" / "thumbnails" / f"{image_id}.jpg"


def _queue_cbir_removal(images: Iterable[dict]) -> None:
    from app.tasks.cbir import cbir_delete_image  # imported lazily: tasks import services

    for img in images:
        if not img.get("cbir_indexed"):
            continue
        try:
            cbir_delete_image.delay(user_id=img["user_id"], image_id=str(img["_id"]), image_path=img["file_path"])
        except Exception as e:
            logger.warning("Failed to queue CBIR deletion for image %s: %s", img["_id"], e)


def delete_analyses(analyses: List[dict], freed: Optional[FreedSpace] = None) -> int:
    """Delete analysis records, their result folders and their references on images."""
    if not analyses:
        return 0
    ids = [a["_id"] for a in analyses]
    id_strings = [str(i) for i in ids]
    get_analyses_collection().delete_many({"_id": {"$in": ids}})
    get_images_collection().update_many(
        {"analysis_ids": {"$in": id_strings}}, {"$pull": {"analysis_ids": {"$in": id_strings}}}
    )
    for analysis in analyses:
        remove_tree(analysis_output_dir(analysis["user_id"], str(analysis["_id"]), str(analysis.get("type", ""))),
                    freed)
    return len(analyses)


def delete_images(images: List[dict], freed: Optional[FreedSpace] = None) -> dict:
    """
    Delete images and everything derived from them (panels included).

    Callers check ownership and business rules (e.g. extracted images can only
    be deleted together with their document).
    """
    if not images:
        return {"images_deleted": 0, "annotations_deleted": 0, "relationships_deleted": 0, "analyses_deleted": 0}

    images_col = get_images_collection()
    by_id = {str(img["_id"]): img for img in images}
    panels = list(images_col.find({"source_image_id": {"$in": list(by_id)}, "source_type": "panel"}))
    for panel in panels:
        by_id.setdefault(str(panel["_id"]), panel)

    all_images = list(by_id.values())
    ids = list(by_id)
    user_ids = {img["user_id"] for img in all_images}

    _queue_cbir_removal(all_images)

    annotations = get_single_annotations_collection().delete_many({"image_id": {"$in": ids}}).deleted_count
    annotations += get_dual_annotations_collection().delete_many(
        {"$or": [{"source_image_id": {"$in": ids}}, {"target_image_id": {"$in": ids}}]}
    ).deleted_count
    relationships = get_relationships_collection().delete_many(
        {"$or": [{"image1_id": {"$in": ids}}, {"image2_id": {"$in": ids}}]}
    ).deleted_count
    analyses = list(get_analyses_collection().find(
        {"user_id": {"$in": list(user_ids)},
         "$or": [{"source_image_id": {"$in": ids}}, {"target_image_id": {"$in": ids}}]}
    ))
    own_freed = freed is None
    freed = freed or FreedSpace()
    analyses_deleted = delete_analyses(analyses, freed)
    images_col.delete_many({"_id": {"$in": [ObjectId(i) for i in ids]}})

    for img in all_images:
        remove_file(img.get("file_path"), freed)
        remove_file(str(thumbnail_path(img["user_id"], str(img["_id"]))), freed)
    for image_id, img in by_id.items():
        _remove_dir_if_empty(UPLOAD_DIR / img["user_id"] / "images" / "panels" / image_id)
    if own_freed:
        freed.release()

    return {
        "images_deleted": len(all_images),
        "annotations_deleted": annotations,
        "relationships_deleted": relationships,
        "analyses_deleted": analyses_deleted,
    }


def delete_document(doc: dict) -> dict:
    """
    Delete a document, its extracted images (with everything derived from
    them), its PDF and its extraction folder.

    Uploaded images merely linked to the document are kept and unlinked; a
    watermark-removed copy is a separate document and is kept.
    """
    doc_id = str(doc["_id"])
    user_id = doc["user_id"]
    images_col = get_images_collection()

    extracted = list(images_col.find({"document_id": doc_id, "user_id": user_id, "source_type": "extracted"}))
    freed = FreedSpace()
    result = delete_images(extracted, freed)
    images_col.update_many(
        {"document_id": doc_id, "user_id": user_id, "source_type": {"$ne": "extracted"}},
        {"$set": {"document_id": None}},
    )
    get_documents_collection().delete_one({"_id": doc["_id"]})

    remove_file(doc.get("file_path"), freed)
    remove_tree(UPLOAD_DIR / user_id / "images" / "extracted" / doc_id, freed)
    freed.release()
    result["deleted_id"] = doc_id
    return result


def delete_user_account(user_id: str) -> dict:
    """Delete every record and file belonging to a user, then the user."""
    from app.tasks.cbir import cbir_delete_user_data  # imported lazily: tasks import services

    counts = {}
    for name, collection in (
        ("documents", get_documents_collection()),
        ("images", get_images_collection()),
        ("single_annotations", get_single_annotations_collection()),
        ("dual_annotations", get_dual_annotations_collection()),
        ("analyses", get_analyses_collection()),
        ("relationships", get_relationships_collection()),
        ("jobs", get_jobs_collection()),
        ("indexing_jobs", get_indexing_jobs_collection()),
    ):
        counts[name] = collection.delete_many({"user_id": user_id}).deleted_count

    try:
        cbir_delete_user_data.delay(user_id=user_id)
    except Exception as e:
        logger.warning("Failed to queue CBIR cleanup for user %s: %s", user_id, e)

    remove_tree(UPLOAD_DIR / user_id)
    get_users_collection().delete_one({"_id": ObjectId(user_id)})
    logger.info("Deleted account %s: %s", user_id, counts)
    return counts


def request_account_deletion(user_id: str) -> None:
    """
    Disable the account and revoke its tokens immediately, then delete its data
    in the background (inline if the task queue is unavailable).
    """
    from datetime import datetime, timezone

    from app.tasks.maintenance import delete_user_account_task  # imported lazily: tasks import services

    get_users_collection().update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"is_active": False, "deletion_requested_at": datetime.now(timezone.utc)},
         "$inc": {"token_version": 1}},
    )
    try:
        delete_user_account_task.delay(user_id=user_id)
    except Exception as e:
        logger.warning("Could not queue deletion of account %s (%s); deleting inline", user_id, e)
        delete_user_account(user_id)
