"""
Storage Quota Configuration

This module defines storage quota limits for the ELIES system.
All limits are in bytes. Modify these values to adjust quotas globally.

To modify quotas:
1. Change the constant values below
2. No code changes needed - the system uses these constants everywhere
3. Restart the application
"""

# ============================================================================
# STORAGE QUOTA CONFIGURATION
# ============================================================================

# Default storage quota per user (in bytes)
# 1 GB = 1,073,741,824 bytes
DEFAULT_USER_STORAGE_QUOTA = 1 * 1024 * 1024 * 1024  # 1 GB

# Individual file limits (in bytes)
MAX_PDF_FILE_SIZE = 500 * 1024 * 1024  # 500 MB per PDF
MAX_IMAGE_FILE_SIZE = 100 * 1024 * 1024  # 100 MB per image

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


# ============================================================================
# QUICK REFERENCE
# ============================================================================

"""
QUICK REFERENCE - How to modify quotas:

1. CHANGE DEFAULT QUOTA:
   DEFAULT_USER_STORAGE_QUOTA = 2 * 1024 * 1024 * 1024  # Change to 2 GB
   
  
  2. CHANGE PDF FILE LIMIT:
     MAX_PDF_FILE_SIZE = 100 * 1024 * 1024  # Change to 100 MB
     
  3. CHANGE IMAGE FILE LIMIT:
     MAX_IMAGE_FILE_SIZE = 20 * 1024 * 1024  # Change to 20 MB

After modifying, restart the application:
   python -m uvicorn app.main:app --reload
"""