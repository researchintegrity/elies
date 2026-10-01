"""
Storage Quota Configuration

Storage limits for the ELIES system, in bytes. They are configured through
the environment (DEFAULT_USER_STORAGE_QUOTA_GB, PDF_MAX_SIZE_MB,
IMAGE_MAX_SIZE_MB); per-user quotas can also be changed by admins.
"""

# ============================================================================
# STORAGE QUOTA CONFIGURATION
# ============================================================================

import os

_MB = 1024 * 1024
_GB = 1024 * _MB


def _env_number(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be a number, got {value!r}")


# Default storage quota per user (DEFAULT_USER_STORAGE_QUOTA_GB, default 1 GB)
DEFAULT_USER_STORAGE_QUOTA = int(_env_number("DEFAULT_USER_STORAGE_QUOTA_GB", 1) * _GB)

# Individual file limits (PDF_MAX_SIZE_MB default 500, IMAGE_MAX_SIZE_MB default 100)
MAX_PDF_FILE_SIZE = int(_env_number("PDF_MAX_SIZE_MB", 500) * _MB)
MAX_IMAGE_FILE_SIZE = int(_env_number("IMAGE_MAX_SIZE_MB", 100) * _MB)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_bytes(bytes_value: int) -> str:
    """
    Convert bytes to human-readable format
    
    Args:
        bytes_value: Number of bytes
        
    Returns:
        Formatted string (e.g., "1.5 GB", "250 MB")
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} PB"


def get_quota_info(used_bytes: int, quota_bytes: int = DEFAULT_USER_STORAGE_QUOTA) -> dict:
    """
    Get detailed quota information for a user
    
    Args:
        used_bytes: Current storage usage in bytes
        quota_bytes: Total quota in bytes
        
    Returns:
        Dictionary with quota information
    """
    remaining = quota_bytes - used_bytes
    used_percentage = (used_bytes / quota_bytes) * 100 if quota_bytes > 0 else 0
    
    return {
        "used_bytes": used_bytes,
        "used_formatted": format_bytes(used_bytes),
        "quota_bytes": quota_bytes,
        "quota_formatted": format_bytes(quota_bytes),
        "remaining_bytes": max(0, remaining),
        "remaining_formatted": format_bytes(max(0, remaining)),
        "used_percentage": round(used_percentage, 2),
    }
