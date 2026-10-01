"""
Storage quota fields (``user_storage_used`` / ``user_storage_remaining``) for
API responses. Usage is read from the user's stored counter (see
storage_service), never computed by walking the workspace.
"""
from typing import Any, Dict, List

from app.services.storage_service import quota_fields


def augment_with_quota(resource: Dict[str, Any], user_id: str, user_quota: int) -> Dict[str, Any]:
    """Add the user's storage usage to one resource."""
    resource.update(quota_fields(user_id, user_quota))
    return resource


def augment_list_with_quota(resources: List[Dict[str, Any]], user_id: str, user_quota: int) -> List[Dict[str, Any]]:
    """Add the user's storage usage to several resources (read once)."""
    fields = quota_fields(user_id, user_quota)
    for resource in resources:
        resource.update(fields)
    return resources
