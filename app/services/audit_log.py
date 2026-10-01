"""
Audit trail of administrator actions (issue #77).

Each change an admin makes to an account (quota, roles, password reset,
activation, deletion) is stored in the ``admin_audit_log`` collection with
who did it, to whom, what changed and the request ID.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.db.mongodb import get_admin_audit_log_collection
from app.request_context import current_request_id

logger = logging.getLogger(__name__)


def record_admin_action(admin: dict, action: str, target_user: Optional[dict] = None,
                        details: Optional[Dict[str, Any]] = None) -> None:
    """Store one audit entry. A storage failure is logged, never raised: the action already happened."""
    entry = {
        "created_at": datetime.now(timezone.utc),
        "action": action,
        "admin_id": str(admin["_id"]),
        "admin_username": admin.get("username"),
        "target_user_id": str(target_user["_id"]) if target_user else None,
        "target_username": target_user.get("username") if target_user else None,
        "details": details or {},
        "request_id": current_request_id(),
    }
    try:
        get_admin_audit_log_collection().insert_one(entry)
    except Exception as e:
        logger.error("Could not write audit entry %s: %s", entry, e)
