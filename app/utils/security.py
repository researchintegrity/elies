"""
Security utilities for authentication and password handling
"""
import logging
import os
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from bson import ObjectId
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

from app.config.settings import BCRYPT_ROUNDS
from app.db.mongodb import get_users_collection

load_dotenv()

logger = logging.getLogger(__name__)

JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", 24))
JWT_SECRET_MIN_LENGTH = 32

# Values that have shipped in example/config files and must never sign tokens
_KNOWN_PLACEHOLDER_SECRETS = {
    "your-secret-key-change-in-production",
    "your-secret-key-here-change-this-in-production-to-a-strong-random-string",
    "change-me",
    "changeme",
    "secret",
}


def _load_jwt_secret() -> str:
    """
    Read JWT_SECRET and refuse to run with an empty, placeholder or short key.

    An empty or published key lets anyone mint a token for any user, so this
    fails at startup instead of silently running insecurely (issue #51).
    """
    secret = os.getenv("JWT_SECRET", "").strip()
    hint = 'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    if not secret:
        raise RuntimeError(f"JWT_SECRET is not set. {hint}")
    if secret in _KNOWN_PLACEHOLDER_SECRETS:
        raise RuntimeError(f"JWT_SECRET is still the example placeholder. {hint}")
    if len(secret) < JWT_SECRET_MIN_LENGTH:
        raise RuntimeError(f"JWT_SECRET must be at least {JWT_SECRET_MIN_LENGTH} characters. {hint}")
    return secret


JWT_SECRET = _load_jwt_secret()

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=BCRYPT_ROUNDS)

# Bearer token from the Authorization header; missing tokens are handled below
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

_TOKEN_QUERY_PATTERN = re.compile(r"([?&]token=)[^&\s\"]+")


def redact_token_in_url(text: str) -> str:
    """Replace the value of a ``token`` query parameter with ``[REDACTED]``."""
    return _TOKEN_QUERY_PATTERN.sub(r"\1[REDACTED]", text)


class RedactTokenFilter(logging.Filter):
    """Logging filter that strips ``?token=...`` values from access log lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_token_in_url(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        elif isinstance(record.msg, str):
            record.msg = redact_token_in_url(record.msg)
        return True


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash"""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token for a user document.

    Claims: ``sub`` (username, informational), ``uid`` (user id), ``tv`` (the
    user's token_version; bumping it revokes all earlier tokens), ``iat`` and ``exp``.
    """
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(hours=JWT_EXPIRATION_HOURS))
    to_encode = {
        "sub": user["username"],
        "uid": str(user["_id"]),
        "tv": user.get("token_version", 0),
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)


def revoke_user_tokens(user_id) -> None:
    """Invalidate every token issued so far for this user."""
    get_users_collection().update_one(
        {"_id": ObjectId(str(user_id))}, {"$inc": {"token_version": 1}}
    )


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _user_from_token(token: Optional[str]) -> dict:
    """Resolve and authorize the user for a token (valid, current, active)."""
    if not token:
        raise _credentials_exception()

    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.InvalidTokenError:
        raise _credentials_exception()

    user_id = payload.get("uid")
    if not user_id or not ObjectId.is_valid(user_id):
        raise _credentials_exception()

    user = get_users_collection().find_one({"_id": ObjectId(user_id)})
    if user is None or payload.get("tv", 0) != user.get("token_version", 0):
        raise _credentials_exception()

    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    return user


def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> dict:
    """
    Authenticated, active user from the ``Authorization: Bearer`` header.

    Raises:
        HTTPException 401: Missing, invalid, expired or revoked token
        HTTPException 403: The account is disabled
    """
    return _user_from_token(token)


def get_current_user_media(
    token: Optional[str] = Depends(oauth2_scheme),
    token_query: Optional[str] = Query(None, alias="token", include_in_schema=False),
) -> dict:
    """
    Like get_current_user, but also accepts ``?token=`` for file endpoints that
    browsers load directly (``<img src>``, downloads) and cannot send headers.
    Only use it on GET routes that return files.
    """
    return _user_from_token(token or token_query)


# Kept for existing imports: every authenticated user is now checked for is_active
get_current_active_user = get_current_user


def get_current_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Get current admin user (checks if user has admin role)

    Raises:
        HTTPException: If user is not an admin
    """
    user_roles = current_user.get("roles", ["user"])

    if "admin" not in user_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )

    return current_user


def generate_secure_password(length: int = 16) -> str:
    """
    Generate a secure random password.

    Raises:
        ValueError: If length is less than 4.
    """
    if length < 4:
        raise ValueError("Password length must be at least 4 characters")

    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"

    # Ensure at least one lowercase, uppercase, digit, and special char
    password = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    password.extend(secrets.choice(alphabet) for _ in range(length - 4))
    secrets.SystemRandom().shuffle(password)

    return ''.join(password)
