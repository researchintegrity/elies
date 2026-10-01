"""
Admin panel routes for user management

Provides endpoints for administrators to:
- List and view users
- Update user quotas
- Manage user roles (promote/demote)
- Reset user passwords
- Activate/deactivate user accounts
"""
import re
from fastapi import APIRouter, HTTPException, status, Depends, Query
from datetime import datetime
from typing import Optional
from bson import ObjectId
import math
import logging

from app.schemas import (
    AdminUserResponse,
    AdminUserListResponse,
    AdminUpdateQuotaRequest,
    AdminUpdateRoleRequest,
    AdminResetPasswordRequest,
    AdminResetPasswordResponse,
    AdminUpdateUserStatusRequest,
)
from app.utils.security import (
    get_current_admin_user,
    hash_password,
    generate_secure_password,
    revoke_user_tokens,
)
from app.db.mongodb import get_users_collection

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ============================================================================
# USER LISTING ENDPOINTS
# ============================================================================

@router.get("/users", response_model=AdminUserListResponse)
def list_users(
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of users per page"),
    search: Optional[str] = Query(None, max_length=100, description="Search by username or email"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    role: Optional[str] = Query(None, description="Filter by role (e.g., 'admin')"),
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    List all users with pagination and optional filters
    
    - **page**: Page number (default: 1)
    - **page_size**: Users per page (default: 20, max: 100)
    - **search**: Search by username or email (optional)
    - **is_active**: Filter by active status (optional)
    - **role**: Filter by role (optional)
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    # Build query filter
    query = {}
    
    if search:
        # Literal, case-insensitive match: user input is never a regex
        pattern = {"$regex": re.escape(search), "$options": "i"}
        query["$or"] = [
            {"username": pattern},
            {"email": pattern},
            {"full_name": pattern},
        ]
    
    if is_active is not None:
        query["is_active"] = is_active
    
    if role:
        query["roles"] = role
    
    # Get total count for pagination
    total = collection.count_documents(query)
    total_pages = math.ceil(total / page_size) if total > 0 else 1
    
    # Calculate skip
    skip = (page - 1) * page_size
    
    # Fetch users
    users_cursor = collection.find(query).skip(skip).limit(page_size).sort("created_at", -1)
    
    users = []
    for user in users_cursor:
        # Ensure roles field exists (for backwards compatibility)
        if "roles" not in user:
            user["roles"] = ["user"]
        users.append(AdminUserResponse(**user).model_dump(by_alias=True))
    
    logger.info(f"Admin {current_admin['username']} listed users (page {page}, total {total})")
    
    return {
        "users": users,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: str,
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Get detailed information about a specific user
    
    - **user_id**: MongoDB ObjectId of the user
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    user = collection.find_one({"_id": object_id})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Ensure roles field exists (for backwards compatibility)
    if "roles" not in user:
        user["roles"] = ["user"]
    
    logger.info(f"Admin {current_admin['username']} viewed user {user['username']}")
    
    return AdminUserResponse(**user).model_dump(by_alias=True)


# ============================================================================
# USER QUOTA MANAGEMENT
# ============================================================================

@router.patch("/users/{user_id}/quota", response_model=AdminUserResponse)
def update_user_quota(
    user_id: str,
    quota_update: AdminUpdateQuotaRequest,
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Update a user's storage quota
    
    - **user_id**: MongoDB ObjectId of the user
    - **storage_limit_bytes**: New storage limit in bytes
    
    Note: This only changes the limit, not the used storage.
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    # Find the user first
    user = collection.find_one({"_id": object_id})
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update quota
    result = collection.find_one_and_update(
        {"_id": object_id},
        {
            "$set": {
                "storage_limit_bytes": quota_update.storage_limit_bytes,
                "updated_at": datetime.utcnow(),
            }
        },
        return_document=True
    )
    
    # Ensure roles field exists
    if "roles" not in result:
        result["roles"] = ["user"]
    
    old_quota = user.get("storage_limit_bytes", 0)
    new_quota = quota_update.storage_limit_bytes
    logger.info(
        f"Admin {current_admin['username']} updated quota for user {user['username']}: "
        f"{old_quota} -> {new_quota} bytes"
    )
    
    return AdminUserResponse(**result).model_dump(by_alias=True)


# ============================================================================
# USER ROLE MANAGEMENT
# ============================================================================

@router.patch("/users/{user_id}/role", response_model=AdminUserResponse)
def update_user_role(
    user_id: str,
    role_update: AdminUpdateRoleRequest,
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Update a user's roles (promote/demote)
    
    - **user_id**: MongoDB ObjectId of the user
    - **roles**: List of roles to assign (e.g., ["user", "admin"])
    
    Note: Admins cannot remove their own admin privileges to prevent lockout.
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    # Find the target user
    target_user = collection.find_one({"_id": object_id})
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent admin from removing their own admin role
    if str(current_admin["_id"]) == user_id and "admin" not in role_update.roles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove your own admin privileges"
        )
    
    # Update roles
    result = collection.find_one_and_update(
        {"_id": object_id},
        {
            "$set": {
                "roles": role_update.roles,
                "updated_at": datetime.utcnow(),
            }
        },
        return_document=True
    )
    
    # Existing tokens were issued for the old roles
    revoke_user_tokens(object_id)

    old_roles = target_user.get("roles", ["user"])
    logger.info(
        f"Admin {current_admin['username']} updated roles for user {target_user['username']}: "
        f"{old_roles} -> {role_update.roles}"
    )
    
    return AdminUserResponse(**result).model_dump(by_alias=True)


# ============================================================================
# PASSWORD RESET
# ============================================================================

@router.post("/users/{user_id}/reset-password", response_model=AdminResetPasswordResponse)
def reset_user_password(
    user_id: str,
    password_request: AdminResetPasswordRequest = None,
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Reset a user's password
    
    - **user_id**: MongoDB ObjectId of the user
    - **new_password**: (Optional) New password. If not provided, a secure random password is generated.
    
    Note: Admin cannot reset another admin's password (security measure).
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    # Find the target user
    target_user = collection.find_one({"_id": object_id})
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent resetting another admin's password
    target_roles = target_user.get("roles", ["user"])
    if "admin" in target_roles and str(current_admin["_id"]) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot reset another admin's password"
        )
    
    # Generate or use provided password
    password_request = password_request or AdminResetPasswordRequest()
    if password_request.new_password:
        new_password = password_request.new_password
        generated = False
    else:
        new_password = generate_secure_password(16)
        generated = True
    
    # Hash and update password
    hashed_password = hash_password(new_password)
    
    collection.update_one(
        {"_id": object_id},
        {
            "$set": {
                "hashed_password": hashed_password,
                # The user should replace an admin-chosen password (PUT /users/me/password)
                "must_change_password": True,
                "updated_at": datetime.utcnow(),
            }
        }
    )
    
    revoke_user_tokens(object_id)

    logger.info(
        f"Admin {current_admin['username']} reset password for user {target_user['username']} "
        f"(generated: {generated})"
    )
    
    response = {
        "message": f"Password reset successfully for user {target_user['username']}"
    }
    
    if generated:
        response["generated_password"] = new_password
    
    return response


# ============================================================================
# USER STATUS MANAGEMENT
# ============================================================================

@router.patch("/users/{user_id}/status", response_model=AdminUserResponse)
def update_user_status(
    user_id: str,
    status_update: AdminUpdateUserStatusRequest,
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Activate or deactivate a user account
    
    - **user_id**: MongoDB ObjectId of the user
    - **is_active**: True to activate, False to deactivate
    
    Note: Admins cannot deactivate their own account.
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    try:
        object_id = ObjectId(user_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    # Prevent admin from deactivating themselves
    if str(current_admin["_id"]) == user_id and not status_update.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account"
        )
    
    # Find the target user
    target_user = collection.find_one({"_id": object_id})
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Update status
    result = collection.find_one_and_update(
        {"_id": object_id},
        {
            "$set": {
                "is_active": status_update.is_active,
                "updated_at": datetime.utcnow(),
            }
        },
        return_document=True
    )
    
    if not status_update.is_active:
        revoke_user_tokens(object_id)

    # Ensure roles field exists
    if "roles" not in result:
        result["roles"] = ["user"]
    
    action = "activated" if status_update.is_active else "deactivated"
    logger.info(f"Admin {current_admin['username']} {action} user {target_user['username']}")
    
    return AdminUserResponse(**result).model_dump(by_alias=True)


# ============================================================================
# ADMIN STATISTICS
# ============================================================================

@router.get("/stats")
def get_admin_stats(
    current_admin: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Get system statistics for admin dashboard
    
    Returns:
    - Total users
    - Active users
    - Total storage used
    - Admin count
    
    Requires admin privileges.
    """
    collection = get_users_collection()
    
    # Aggregate statistics
    pipeline = [
        {
            "$group": {
                "_id": None,
                "total_users": {"$sum": 1},
                "active_users": {
                    "$sum": {"$cond": [{"$eq": ["$is_active", True]}, 1, 0]}
                },
                "total_storage_used": {"$sum": "$storage_used_bytes"},
                "total_storage_allocated": {"$sum": "$storage_limit_bytes"},
            }
        }
    ]
    
    result = list(collection.aggregate(pipeline))
    
    stats = {
        "total_users": 0,
        "active_users": 0,
        "total_storage_used_bytes": 0,
        "total_storage_allocated_bytes": 0,
        "admin_count": 0,
    }
    
    if result:
        stats["total_users"] = result[0].get("total_users", 0)
        stats["active_users"] = result[0].get("active_users", 0)
        stats["total_storage_used_bytes"] = result[0].get("total_storage_used", 0)
        stats["total_storage_allocated_bytes"] = result[0].get("total_storage_allocated", 0)
    
    # Count admins separately
    stats["admin_count"] = collection.count_documents({"roles": "admin"})
    
    logger.info(f"Admin {current_admin['username']} retrieved system stats")
    
    return stats
