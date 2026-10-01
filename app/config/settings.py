"""
Application-wide configuration settings

This module centralizes all hardcoded configuration values to make the
application more maintainable and configurable. All magic numbers and
strings are defined here as constants.

To customize settings:
1. Modify the constants below
2. Import from this module in your code
3. No need to change multiple files
"""

import os
from pathlib import Path
from typing import Union

# ============================================================================
# FILE STORAGE SETTINGS
# ============================================================================

# Base directory for all user uploads and workspace files




# Path constants
CONTAINER_WORKSPACE_PATH = Path(os.getenv("CONTAINER_WORKSPACE_PATH"))
if CONTAINER_WORKSPACE_PATH is None:
    raise ValueError("CONTAINER_WORKSPACE_PATH environment variable must be set")
EXTRACTION_SUBDIRECTORY = "images/extracted"

# Workspace root directory (can be overridden by environment variable)
HOST_WORKSPACE_PATH = Path(os.getenv("HOST_WORKSPACE_PATH"))
if HOST_WORKSPACE_PATH is None:
    raise ValueError("HOST_WORKSPACE_PATH environment variable must be set")

RUNNING_ENV =os.getenv("ENVIRONMENT", "")
if RUNNING_ENV != "TEST":
    # Use absolute path for workspace to avoid issues with relative paths in different contexts
    UPLOAD_DIR = Path(CONTAINER_WORKSPACE_PATH)
else:
    UPLOAD_DIR = Path(HOST_WORKSPACE_PATH)


# ============================================================================
# EXTRACTION SETTINGS
# ============================================================================

# Docker image for PDF extraction
PDF_EXTRACTOR_DOCKER_IMAGE = "pdf-extractor:latest"

# Docker image for PDF watermark removal (system_modules/watermark-removal)
PDF_WATERMARK_REMOVAL_DOCKER_IMAGE = "pdf-watermark-removal:latest"

# Template for output filename suffix when creating watermark-removed PDFs.
# Use format placeholder `{mode}` for aggressiveness mode.
WATERMARK_REMOVAL_OUTPUT_SUFFIX_TEMPLATE = "_watermark_removed_m{mode}.pdf"

# Working directory inside the watermark-removal Docker container
WATERMARK_REMOVAL_DOCKER_WORKDIR = CONTAINER_WORKSPACE_PATH

# Docker image for panel extraction (system_modules/panel-extractor)
PANEL_EXTRACTOR_DOCKER_IMAGE = "panel-extractor:latest"

# Working directory inside the panel-extractor Docker container
PANEL_EXTRACTION_DOCKER_WORKDIR = CONTAINER_WORKSPACE_PATH

# Panel extraction settings
PANEL_EXTRACTION_TIMEOUT = 600  # 10 minutes (panel extraction can take longer)
MAX_IMAGES_PER_EXTRACTION = 20  # Maximum number of images to process in one batch

# Docker image for Copy-Move Detection - Dense method (system_modules/copy-move-detection)
COPY_MOVE_DETECTION_DOCKER_IMAGE = "copy-move-detection:latest"
COPY_MOVE_DETECTION_TIMEOUT = 600  # 10 minutes
COPY_MOVE_DETECTION_DOCKER_WORKDIR = CONTAINER_WORKSPACE_PATH

# Docker image for Copy-Move Detection - Keypoint method (system_modules/copy-move-detection-keypoint)
COPY_MOVE_KEYPOINT_DOCKER_IMAGE = "copy-move-detection-keypoint:latest"
COPY_MOVE_KEYPOINT_TIMEOUT = 600  # 10 minutes

# Docker image for TruFor Detection (system_modules/TruFor)
TRUFOR_DOCKER_IMAGE = "trufor:latest"
TRUFOR_TIMEOUT = 600  # 10 minutes
TRUFOR_DOCKER_WORKDIR = CONTAINER_WORKSPACE_PATH
TRUFOR_USE_GPU = os.getenv("TRUFOR_USE_GPU", "true").lower() == "true"

# ============================================================================
# CBIR (Content-Based Image Retrieval) SETTINGS
# ============================================================================
# When running inside Docker, use container name 'cbir-service'
# When running locally, use 'localhost:8001'
# The CBIR_SERVICE_HOST is the hostname/IP of the CBIR microservice
CBIR_SERVICE_HOST = os.getenv("CBIR_SERVICE_HOST", "localhost")
CBIR_SERVICE_PORT = int(os.getenv("CBIR_SERVICE_PORT", "8001"))
CBIR_SERVICE_URL = os.getenv(
    "CBIR_SERVICE_URL",
    f"http://{CBIR_SERVICE_HOST}:{CBIR_SERVICE_PORT}"
)
CBIR_TIMEOUT = int(os.getenv("CBIR_TIMEOUT", "120"))  # 2 minutes default

# Batch indexing: number of images to process per chunk for progress updates
INDEXING_BATCH_CHUNK_SIZE = int(os.getenv("INDEXING_BATCH_CHUNK_SIZE", "16"))

# ============================================================================
# PROVENANCE ANALYSIS SETTINGS
# ============================================================================
# When running inside Docker, use container name 'provenance-service'
# When running locally, use 'localhost:8002'
PROVENANCE_SERVICE_HOST = os.getenv("PROVENANCE_SERVICE_HOST", "localhost")
PROVENANCE_SERVICE_PORT = int(os.getenv("PROVENANCE_SERVICE_PORT", "8002"))
PROVENANCE_SERVICE_URL = os.getenv(
    "PROVENANCE_SERVICE_URL",
    f"http://{PROVENANCE_SERVICE_HOST}:{PROVENANCE_SERVICE_PORT}"
)
PROVENANCE_TIMEOUT = int(os.getenv("PROVENANCE_TIMEOUT", "600"))  # 10 minutes default

# Extraction timeouts (in seconds)
DOCKER_EXTRACTION_TIMEOUT = 300  # 5 minutes
DOCKER_COMPOSE_EXTRACTION_TIMEOUT = 300  # 5 minutes
DOCKER_IMAGE_CHECK_TIMEOUT = 10  # Check if image exists



# ============================================================================
# CELERY TASK SETTINGS
# ============================================================================

# Task timing (in seconds)
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes hard limit
CELERY_TASK_SOFT_TIME_LIMIT = 25 * 60  # 25 minutes soft limit
CELERY_TASK_DEFAULT_RETRY_DELAY = 60  # 1 minute

# Task retries
CELERY_MAX_RETRIES = 3
CELERY_RETRY_BACKOFF_BASE = 2  # Exponential backoff multiplier

# Result settings
CELERY_RESULT_EXPIRES = 3600  # 1 hour

# Job monitoring settings
JOB_RETENTION_DAYS = int(os.getenv("JOB_RETENTION_DAYS", "7"))  # Days to retain job logs

# Redis connection timeouts
CELERY_REDIS_SOCKET_CONNECT_TIMEOUT = 5
CELERY_REDIS_SOCKET_TIMEOUT = 5

# Supported image file extensions for extraction
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.tiff', '.bmp')

# MIME type mappings for extracted images
IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.tiff': 'image/tiff',
    '.bmp': 'image/bmp'
}

# ============================================================================
# USER VALIDATION SETTINGS
# ============================================================================

# Username constraints
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 50

# Password constraints
PASSWORD_MIN_LENGTH = 4

# Full name constraints
FULL_NAME_MAX_LENGTH = 100

# ============================================================================
# IMAGE PROCESSING SETTINGS
# ============================================================================

# Thumbnail generation settings
DEFAULT_THUMBNAIL_SIZE = (300, 300)  # Max width x height in pixels
THUMBNAIL_JPEG_QUALITY = 85  # JPEG quality (1-100)

# Largest image (width * height) accepted on upload or decoded for thumbnails.
# Guards against decompression bombs; raise it for very large microscopy scans.
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", str(200_000_000)))

# Maximum number of files accepted by one batch image upload request
MAX_BATCH_UPLOAD_FILES = int(os.getenv("MAX_BATCH_UPLOAD_FILES", "200"))

# Password hashing settings
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))  # bcrypt cost factor (tests lower it for speed)

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def get_extraction_path_template() -> str:
    """
    Get the path template for extracted images.
    
    Returns:
        Template string: {user_id}/images/extracted/{doc_id}/{filename}
    """
    return f"{{user_id}}/{EXTRACTION_SUBDIRECTORY}/{{doc_id}}/{{filename}}"


def get_container_path_prefix() -> Path:
    """
    Get the prefix used for container paths (for path detection).
    
    Returns:
        Container path prefix as Path object.
    """
    return CONTAINER_WORKSPACE_PATH


def is_container_path(path: Union[str, Path]) -> bool:
    """
    Check if a path is running inside a container.
    
    Args:
        path: File path to check.
        
    Returns:
        True if path starts with container prefix, False otherwise.
    """
    return Path(path).is_relative_to(CONTAINER_WORKSPACE_PATH)

def convert_container_path_to_host(container_path: Union[str, Path]) -> Path:
    """
    Convert a container path to a relative workspace path.
    
    Args:
        container_path: Path inside container (starts with /workspace).
        
    Returns:
        Relative path from workspace root (without HOST_WORKSPACE_PATH prefix).
        
    Raises:
        ValueError: If path is not under container workspace path.
    """
    container_path = Path(container_path) if not isinstance(container_path, Path) else container_path
    if is_container_path(container_path):
        try:
            rel_path = container_path.relative_to(CONTAINER_WORKSPACE_PATH)
        except ValueError:
            raise ValueError(
                f"Path {container_path} is not under container workspace path {CONTAINER_WORKSPACE_PATH}"
            )
        return HOST_WORKSPACE_PATH / rel_path
    return container_path

def convert_host_path_to_container(path: Union[str, Path]) -> Path:
    """
    Ensure a path is in container format.
    
    Args:
        path: Path to ensure is in container format.
        
    Returns:
        Path in container format.
        
    Raises:
        ValueError: If path is not under host workspace path.
    """
    path = Path(path) if not isinstance(path, Path) else path
    if is_container_path(path):
        return path
    try:
        rel_path = path.relative_to(HOST_WORKSPACE_PATH)
    except ValueError:
        raise ValueError(f"Path {path} is not under host workspace path {HOST_WORKSPACE_PATH}")
    return CONTAINER_WORKSPACE_PATH / rel_path