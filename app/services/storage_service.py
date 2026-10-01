"""
Per-user storage accounting (issue #71).

``users.storage_used_bytes`` is a running total of the files in the user's
workspace directory (``UPLOAD_DIR/<user_id>``). Everything stored there counts
against the quota: uploaded PDFs and images, extracted and panel images,
thumbnails, analysis results and watermark-removed PDFs.

- Uploads reserve their size with one conditional ``$inc`` before the record
  is created, so concurrent uploads cannot overshoot the quota together.
- Files produced by the server (extraction, analyses, thumbnails) are added
  after they are written. They are never refused: a user may end up slightly
  over quota and must delete something before uploading again.
- Deletions release the bytes actually freed.
- ``reconcile_storage`` recomputes the total from disk; Celery beat runs it
  daily for every user to correct any drift.

Reading the quota is a single indexed lookup: no request walks the workspace.
"""
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional, Union

from bson import ObjectId

from app.config.settings import UPLOAD_DIR
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA, format_bytes
from app.db.mongodb import get_users_collection
from app.exceptions import StorageQuotaExceededError

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def storage_limit(user: dict) -> int:
    return user.get("storage_limit_bytes", DEFAULT_USER_STORAGE_QUOTA)


def path_size(path: Optional[PathLike]) -> int:
    """Size in bytes of a file, or of every file below a directory (0 if missing)."""
    if not path:
        return 0
    path = Path(path)
    try:
        if path.is_file():
            return path.stat().st_size
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError as e:
        logger.warning("Could not measure %s: %s", path, e)
    return 0


def workspace_owner(path: PathLike) -> Optional[str]:
    """The user id owning a workspace path (its first component below UPLOAD_DIR)."""
    try:
        parts = Path(path).resolve().relative_to(UPLOAD_DIR.resolve()).parts
    except (OSError, ValueError):
        return None
    return parts[0] if len(parts) > 1 else None


def get_storage_used(user_id: str) -> int:
    user = get_users_collection().find_one({"_id": ObjectId(user_id)}, {"storage_used_bytes": 1})
    return max(0, (user or {}).get("storage_used_bytes", 0))


def quota_fields(user_id: str, limit: int) -> Dict[str, int]:
    """``user_storage_used`` / ``user_storage_remaining`` for API responses."""
    used = get_storage_used(user_id)
    return {"user_storage_used": used, "user_storage_remaining": max(0, limit - used)}


def user_quota_fields(user_id: str) -> Dict[str, int]:
    """quota_fields() when only the user id is at hand (one query)."""
    user = get_users_collection().find_one({"_id": ObjectId(user_id)},
                                           {"storage_used_bytes": 1, "storage_limit_bytes": 1}) or {}
    used = max(0, user.get("storage_used_bytes", 0))
    return {"user_storage_used": used, "user_storage_remaining": max(0, storage_limit(user) - used)}


def reserve_storage(user: dict, size: int) -> None:
    """
    Atomically charge ``size`` bytes to the user if it fits in their quota.

    Raises:
        StorageQuotaExceededError (HTTP 413)
    """
    limit = storage_limit(user)
    user_oid = ObjectId(str(user["_id"]))
    if size <= limit:
        result = get_users_collection().update_one(
            {"_id": user_oid, "$or": [
                {"storage_used_bytes": {"$lte": limit - size}},
                {"storage_used_bytes": {"$exists": False}},
            ]},
            {"$inc": {"storage_used_bytes": size}},
        )
        if result.modified_count:
            return
    remaining = max(0, limit - get_storage_used(str(user["_id"])))
    raise StorageQuotaExceededError(
        f"Storage quota exceeded. File size: {format_bytes(size)}, "
        f"Remaining quota: {format_bytes(remaining)}. Total quota: {format_bytes(limit)}",
        file_size=size, remaining=remaining, quota=limit,
    )


def add_storage(user_id: str, size: int) -> None:
    """Charge (or, if negative, credit) bytes without a quota check."""
    if not size or not ObjectId.is_valid(user_id):
        return
    users = get_users_collection()
    try:
        users.update_one({"_id": ObjectId(user_id)}, {"$inc": {"storage_used_bytes": size}})
        if size < 0:
            users.update_one({"_id": ObjectId(user_id), "storage_used_bytes": {"$lt": 0}},
                             {"$set": {"storage_used_bytes": 0}})
    except Exception as e:  # accounting must not fail the operation; reconciliation repairs it
        logger.warning("Could not update storage usage of user %s by %d bytes: %s", user_id, size, e)


def release_storage(user_id: str, size: int) -> None:
    add_storage(user_id, -size)


@contextmanager
def track_writes(user_id: str, path: PathLike) -> Iterator[None]:
    """
    Charge the user for the growth of ``path`` (a file or directory) during
    the block, e.g. a tool writing its results. Shrinking is credited, so
    re-running a tool over its previous output is not counted twice.
    """
    before = path_size(path)
    try:
        yield
    finally:
        add_storage(user_id, path_size(path) - before)


def reconcile_storage(user_id: str) -> int:
    """Recompute a user's usage from disk and store it. Returns the total."""
    used = path_size(UPLOAD_DIR / user_id)
    get_users_collection().update_one(
        {"_id": ObjectId(user_id)},
        {"$set": {"storage_used_bytes": used, "storage_reconciled_at": datetime.now(timezone.utc)}},
    )
    return used


def reconcile_all_storage() -> Dict[str, int]:
    """Reconcile every user; returns the users whose stored total was wrong."""
    corrected = {}
    for user in get_users_collection().find({}, {"_id": 1, "storage_used_bytes": 1}):
        user_id = str(user["_id"])
        used = reconcile_storage(user_id)
        if used != user.get("storage_used_bytes", 0):
            corrected[user_id] = used - user.get("storage_used_bytes", 0)
    if corrected:
        logger.info("Storage usage corrected for %d users: %s", len(corrected), corrected)
    return corrected
