"""
Custom exceptions for ELIES backend.

This module provides a hierarchy of domain-specific exceptions that are
automatically converted to HTTP responses by FastAPI exception handlers.

Usage:
    from app.exceptions import ResourceNotFoundError, ValidationError
    
    # In services:
    raise ResourceNotFoundError("Document", doc_id)
    raise ValidationError("Invalid email format")
    
    # FastAPI exception handler in main.py converts these to HTTPException
"""
from typing import Optional


class ELIESException(Exception):
    """
    Base exception for all ELIES application errors.
    
    All custom exceptions inherit from this class.
    The exception handler in main.py converts these to HTTP responses.
    
    Attributes:
        status_code: HTTP status code to return (default: 500).
        message: Human-readable error message.
    """
    status_code: int = 500
    
    def __init__(self, message: str = "An unexpected error occurred"):
        self.message = message
        super().__init__(self.message)
    
    def __str__(self) -> str:
        return self.message


class ValidationError(ELIESException):
    """
    Invalid input or request data (HTTP 400).
    
    Use for:
    - Invalid ObjectId format
    - Invalid field values
    - Missing required fields
    - Business rule violations
    
    Examples:
        raise ValidationError("Invalid document ID format")
        raise ValidationError("Email address is required")
        raise ValidationError("At least one image must be selected", field="image_ids")
    """
    status_code = 400
    
    def __init__(self, message: str, field: Optional[str] = None):
        self.field = field
        if field:
            message = f"{field}: {message}"
        super().__init__(message)


class ResourceNotFoundError(ELIESException):
    """
    Requested resource does not exist (HTTP 404).
    
    Use when a specific resource cannot be found by ID.
    
    Examples:
        raise ResourceNotFoundError("Document", "507f1f77bcf86cd799439011")
        raise ResourceNotFoundError("Image", image_id)
    """
    status_code = 404
    
    def __init__(
        self, 
        resource_type: str, 
        resource_id: Optional[str] = None,
        message: Optional[str] = None
    ):
        self.resource_type = resource_type
        self.resource_id = resource_id
        
        if message:
            super().__init__(message)
        elif resource_id:
            super().__init__(f"{resource_type} not found: {resource_id}")
        else:
            super().__init__(f"{resource_type} not found")


class AuthorizationError(ELIESException):
    """
    User is not authorized to perform action (HTTP 403).
    
    Use when user is authenticated but lacks permission.
    
    Examples:
        raise AuthorizationError("You don't have permission to delete this document")
        raise AuthorizationError("Admin privileges required", action="delete_user")
    """
    status_code = 403
    
    def __init__(self, message: str = "Permission denied", action: Optional[str] = None):
        self.action = action
        super().__init__(message)


class StorageQuotaExceededError(ELIESException):
    """
    User has exceeded storage quota (HTTP 413).
    
    Use when file upload would exceed storage limits.
    
    Examples:
        raise StorageQuotaExceededError(
            file_size=10_000_000,
            remaining=5_000_000,
            quota=1_073_741_824
        )
    """
    status_code = 413
    
    def __init__(
        self,
        message: Optional[str] = None,
        file_size: Optional[int] = None,
        remaining: Optional[int] = None,
        quota: Optional[int] = None
    ):
        self.file_size = file_size
        self.remaining = remaining
        self.quota = quota
        
        if message:
            super().__init__(message)
        elif file_size and remaining and quota:
            super().__init__(
                f"Storage quota exceeded. File size: {file_size} bytes, "
                f"Remaining: {remaining} bytes, Total quota: {quota} bytes"
            )
        else:
            super().__init__("Storage quota exceeded")


class ExternalServiceError(ELIESException):
    """
    External service (Docker, CBIR, etc.) failed (HTTP 502).
    
    Use for failures in external dependencies.
    
    Examples:
        raise ExternalServiceError("cbir", "Connection refused")
        raise ExternalServiceError("docker", "Container failed to start")
    """
    status_code = 502
    
    def __init__(self, service_name: str, message: Optional[str] = None):
        self.service_name = service_name
        if message:
            super().__init__(f"{service_name} service error: {message}")
        else:
            super().__init__(f"{service_name} service unavailable")


class ConflictError(ELIESException):
    """
    Resource conflict (HTTP 409).
    
    Use for duplicate entries or conflicting state.
    
    Examples:
        raise ConflictError("Username already exists")
        raise ConflictError("Document is currently being processed")
    """
    status_code = 409
    
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(message)


class FileOperationError(ELIESException):
    """
    File system operation failed (HTTP 500).
    
    Use for file read/write/delete failures.
    
    Examples:
        raise FileOperationError("delete", "/path/to/file", "Permission denied")
    """
    status_code = 500
    
    def __init__(
        self, 
        operation: str, 
        path: Optional[str] = None, 
        reason: Optional[str] = None
    ):
        self.operation = operation
        self.path = path
        self.reason = reason
        
        parts = [f"Failed to {operation} file"]
        if path:
            parts.append(f": {path}")
        if reason:
            parts.append(f" ({reason})")
        super().__init__("".join(parts))
