"""
Authentication routes for user registration and login
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pymongo.errors import DuplicateKeyError

from app.config.settings import (
    LOGIN_FAILURE_WINDOW_SECONDS,
    LOGIN_MAX_FAILURES,
    REGISTRATION_MAX_PER_HOUR,
)
from app.config.storage_quota import DEFAULT_USER_STORAGE_QUOTA
from app.db.mongodb import get_users_collection
from app.schemas import TokenResponse, UserRegister, UserResponse
from app.utils.rate_limit import SlidingWindowLimiter
from app.utils.security import (
    email_query,
    JWT_EXPIRATION_HOURS,
    create_access_token,
    hash_password,
    verify_password_or_dummy,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

# Failed logins per (client IP, account) and per client IP; registrations per IP
login_failures_by_account = SlidingWindowLimiter("login-account", LOGIN_MAX_FAILURES, LOGIN_FAILURE_WINDOW_SECONDS)
login_failures_by_ip = SlidingWindowLimiter("login-ip", LOGIN_MAX_FAILURES * 5, LOGIN_FAILURE_WINDOW_SECONDS)
registrations_by_ip = SlidingWindowLimiter("register-ip", REGISTRATION_MAX_PER_HOUR, 3600)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many_requests(retry_after: int, what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many {what}. Try again in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


def _token_response(user: dict) -> dict:
    if "roles" not in user:
        user["roles"] = ["user"]
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "user": UserResponse(**user).model_dump(by_alias=True),
        "expires_in": int(timedelta(hours=JWT_EXPIRATION_HOURS).total_seconds()),
    }


@router.post("/register", response_model=TokenResponse)
def register(user_data: UserRegister, request: Request) -> dict:
    """
    Register a new user

    - **username**: Unique username (3-50 characters: letters, digits, '.', '_', '-')
    - **email**: Valid email address
    - **password**: Password (see PASSWORD_MIN_LENGTH, at most 72 bytes)
    - **full_name**: Optional full name
    """
    client_ip = _client_ip(request)
    retry_after = registrations_by_ip.retry_after(client_ip)
    if retry_after:
        raise _too_many_requests(retry_after, "registrations from this address")
    registrations_by_ip.hit(client_ip)

    collection = get_users_collection()
    email = user_data.email.lower()

    existing_user = collection.find_one(
        {"$or": [{"username": user_data.username}, {"email": email_query(email)}]}
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )

    now = datetime.now(timezone.utc)
    user_doc = {
        "username": user_data.username,
        "email": email,
        "hashed_password": hash_password(user_data.password),
        "full_name": user_data.full_name,
        "is_active": True,
        "roles": ["user"],  # Default role for new users
        "token_version": 0,
        "must_change_password": False,
        "storage_used_bytes": 0,  # Initialize storage usage tracking
        "storage_limit_bytes": DEFAULT_USER_STORAGE_QUOTA,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None
    }

    try:
        result = collection.insert_one(user_doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered"
        )

    return _token_response(collection.find_one({"_id": result.inserted_id}))


@router.post("/login", response_model=TokenResponse)
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()) -> dict:
    """
    Login with username and password

    - **username**: Username or email
    - **password**: User password

    Returns JWT access token and user information. Repeated failures from the
    same client are throttled (HTTP 429 with Retry-After).
    """
    client_ip = _client_ip(request)
    account_key = f"{client_ip}|{form_data.username.lower()}"
    retry_after = login_failures_by_account.retry_after(account_key) or login_failures_by_ip.retry_after(client_ip)
    if retry_after:
        raise _too_many_requests(retry_after, "failed login attempts")

    collection = get_users_collection()
    user = collection.find_one(
        {"$or": [{"username": form_data.username}, {"email": email_query(form_data.username)}]}
    )

    if not verify_password_or_dummy(form_data.password, user["hashed_password"] if user else None):
        login_failures_by_account.hit(account_key)
        login_failures_by_ip.hit(client_ip)
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

    login_failures_by_account.reset(account_key)

    now = datetime.now(timezone.utc)
    collection.update_one({"_id": user["_id"]}, {"$set": {"last_login_at": now}})
    user["last_login_at"] = now

    return _token_response(user)
