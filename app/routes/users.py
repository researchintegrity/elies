"""
User management routes
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.mongodb import get_users_collection
from app.routes.auth import _token_response, email_query
from app.schemas import (
    MessageResponse,
    PasswordChangeRequest,
    TokenResponse,
    UserResponse,
    UserUpdate,
)
from app.utils.security import (
    get_current_active_user,
    get_current_admin_user,
    hash_password,
    revoke_user_tokens,
    verify_password,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: dict = Depends(get_current_active_user)) -> dict:
    """
    Get current authenticated user information
    
    Returns the profile of the currently authenticated user
    """
    # Ensure roles field exists for backwards compatibility
    if "roles" not in current_user:
        current_user["roles"] = ["user"]
    return UserResponse(**current_user).dict(by_alias=True)


@router.put("/me", response_model=UserResponse)
def update_current_user(
    update_data: UserUpdate,
    current_user: dict = Depends(get_current_active_user)
) -> dict:
    """
    Update current user information
    
    - **full_name**: Update user's full name
    - **email**: Update user's email address
    """
    collection = get_users_collection()
    
    # Prepare update data
    update_dict = {
        "updated_at": datetime.now(timezone.utc)
    }

    if update_data.full_name is not None:
        update_dict["full_name"] = update_data.full_name

    if update_data.email is not None:
        email = update_data.email.lower()
        existing_user = collection.find_one({
            "email": email_query(email),
            "_id": {"$ne": current_user["_id"]}
        })

        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )

        update_dict["email"] = email
    
    # Update user
    result = collection.find_one_and_update(
        {"_id": current_user["_id"]},
        {"$set": update_dict},
        return_document=True
    )
    
    return UserResponse(**result).dict(by_alias=True)


@router.delete("/me", response_model=MessageResponse)
def delete_current_user(current_user: dict = Depends(get_current_active_user)) -> dict:
    """
    Delete current user account
    
    Permanently deletes the authenticated user's account and all associated data
    """
    collection = get_users_collection()
    
    collection.delete_one({"_id": current_user["_id"]})
    
    return {"message": "User account deleted successfully"}


@router.put("/me/password", response_model=TokenResponse)
def change_password(
    request: PasswordChangeRequest,
    current_user: dict = Depends(get_current_active_user)
) -> dict:
    """
    Change the current user's password.

    Requires the current password. All previously issued tokens are revoked;
    the response contains a fresh token for this session.
    """
    if not verify_password(request.current_password, current_user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    if request.new_password == request.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password"
        )

    collection = get_users_collection()
    collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {
            "hashed_password": hash_password(request.new_password),
            "must_change_password": False,
            "updated_at": datetime.now(timezone.utc),
        }}
    )
    revoke_user_tokens(current_user["_id"])
    return _token_response(collection.find_one({"_id": current_user["_id"]}))


@router.get("/{username}", response_model=UserResponse)
def get_user_by_username(
    username: str,
    current_user: dict = Depends(get_current_admin_user)
) -> dict:
    """
    Get user information by username (admins only).

    Profiles include email, roles and quota, so they are not visible to
    regular users.
    """
    collection = get_users_collection()
    
    user = collection.find_one({"username": username})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return UserResponse(**user).dict(by_alias=True)
