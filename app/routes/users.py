"""
User management routes
"""
from fastapi import APIRouter, HTTPException, status, Depends
from datetime import datetime

from app.schemas import UserResponse, UserUpdate, MessageResponse
from app.utils.security import get_current_user, get_current_active_user
from app.db.mongodb import get_users_collection

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
        "updated_at": datetime.utcnow()
    }
    
    if update_data.full_name is not None:
        update_dict["full_name"] = update_data.full_name
    
    if update_data.email is not None:
        # Check if email already exists
        existing_user = collection.find_one({
            "email": update_data.email,
            "_id": {"$ne": current_user["_id"]}
        })
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        
        update_dict["email"] = update_data.email
    
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


@router.get("/{username}", response_model=UserResponse)
def get_user_by_username(
    username: str,
    current_user: dict = Depends(get_current_active_user)
) -> dict:
    """
    Get user information by username
    
    Note: Only authenticated users can view other user profiles
    """
    collection = get_users_collection()
    
    user = collection.find_one({"username": username})
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return UserResponse(**user).dict(by_alias=True)
