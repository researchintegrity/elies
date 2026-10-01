"""
Authentication routes for user registration and login
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.errors import DuplicateKeyError

from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_users_collection
from app.schemas import TokenResponse, UserRegister, UserResponse
from app.utils.security import (
    JWT_EXPIRATION_HOURS,
    create_access_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=TokenResponse)
def register(user_data: UserRegister) -> dict:
    """
    Register a new user
    
    - **username**: Unique username (3-50 characters)
    - **email**: Valid email address
    - **password**: Password (minimum 4 characters)
    - **full_name**: Optional full name
    """
    collection = get_users_collection()
    
    # Check if user already exists
    existing_user = collection.find_one(
        {"$or": [{"username": user_data.username}, {"email": user_data.email}]}
    )
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )
    
    # Create new user document
    now = datetime.now(timezone.utc)
    user_doc = {
        "username": user_data.username,
        "email": user_data.email,
        "hashed_password": hash_password(user_data.password),
        "full_name": user_data.full_name,
        "is_active": True,
        "roles": ["user"],  # Default role for new users
        "storage_used_bytes": 0,  # Initialize storage usage tracking
        "storage_limit_bytes": DEFAULT_USER_STORAGE_QUOTA,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None
    }
    
    try:
        result = collection.insert_one(user_doc)
        
        # Get created user
        created_user = collection.find_one({"_id": result.inserted_id})
        
        # Create token
        access_token = create_access_token(created_user)
        expires_delta = timedelta(hours=JWT_EXPIRATION_HOURS)
        
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserResponse(**created_user).dict(by_alias=True),
            "expires_in": int(expires_delta.total_seconds())
        }
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """
    Login with username and password
    
    - **username**: Username or email
    - **password**: User password
    
    Returns JWT access token and user information
    """
    collection = get_users_collection()
    
    # Find user by username or email
    user = collection.find_one(
        {"$or": [{"username": form_data.username}, {"email": form_data.username}]}
    )
    
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )
    
    # Update last_login_at timestamp
    now = datetime.now(timezone.utc)
    collection.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login_at": now}}
    )
    user["last_login_at"] = now
    
    # Ensure roles field exists for backwards compatibility
    if "roles" not in user:
        user["roles"] = ["user"]
    
    # Create token
    access_token = create_access_token(user)
    expires_delta = timedelta(hours=JWT_EXPIRATION_HOURS)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserResponse(**user).dict(by_alias=True),
        "expires_in": int(expires_delta.total_seconds())
    }
