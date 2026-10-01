"""
Application-wide configuration settings

Every value that can differ between deployments is read from the environment
here (documented in .env.example), so the API and the Celery workers share one
validated configuration. Constants that are not deployment-specific live here
too, to keep magic numbers out of the code.
"""

import os
from pathlib import Path
from typing import List, Union


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {value!r}")


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be a number, got {value!r}")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _required_path(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} environment variable must be set (see .env.example)")
    return Path(value)


# ============================================================================
# FILE STORAGE SETTINGS
# ============================================================================

# Workspace root as seen by this process (inside the API/worker containers)
CONTAINER_WORKSPACE_PATH = _required_path("CONTAINER_WORKSPACE_PATH")
# The same directory as seen by the Docker daemon on the host (for tool mounts)
HOST_WORKSPACE_PATH = _required_path("HOST_WORKSPACE_PATH")

EXTRACTION_SUBDIRECTORY = "images/extracted"

# Base directory for all user uploads and workspace files
UPLOAD_DIR = CONTAINER_WORKSPACE_PATH

# ============================================================================
# LOGGING, CORS AND SERVICE CONNECTIONS
# ============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Browser origins allowed to call the API (comma-separated)
ALLOWED_ORIGINS = _env_list(
    "ALLOWED_ORIGINS",
    ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"],
)

# Redis (Celery broker and result backend)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = _env_int("REDIS_PORT", 6379)
REDIS_DB = _env_int("REDIS_DB", 0)
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")


def _redis_url(db: int) -> str:
    auth = f":{REDIS_PASSWORD}@" if REDIS_PASSWORD else ""
    return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"


CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL") or _redis_url(REDIS_DB)
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND") or _redis_url(REDIS_DB + 1)


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
TRUFOR_USE_GPU = _env_bool("TRUFOR_USE_GPU", False)

# ============================================================================
# CBIR (Content-Based Image Retrieval) SETTINGS
# ============================================================================
# When running inside Docker, use container name 'cbir-service'
# When running locally, use 'localhost:8001'
# The CBIR_SERVICE_HOST is the hostname/IP of the CBIR microservice
CBIR_SERVICE_HOST = os.getenv("CBIR_SERVICE_HOST", "localhost")
CBIR_SERVICE_PORT = _env_int("CBIR_SERVICE_PORT", 8001)
CBIR_SERVICE_URL = os.getenv(
    "CBIR_SERVICE_URL",
    f"http://{CBIR_SERVICE_HOST}:{CBIR_SERVICE_PORT}"
)
CBIR_TIMEOUT = _env_int("CBIR_TIMEOUT", 120)  # 2 minutes default

# Batch indexing: number of images to process per chunk for progress updates
INDEXING_BATCH_CHUNK_SIZE = _env_int("INDEXING_BATCH_CHUNK_SIZE", 16)

# ============================================================================
# PROVENANCE ANALYSIS SETTINGS
# ============================================================================
# When running inside Docker, use container name 'provenance-service'
# When running locally, use 'localhost:8002'
PROVENANCE_SERVICE_HOST = os.getenv("PROVENANCE_SERVICE_HOST", "localhost")
PROVENANCE_SERVICE_PORT = _env_int("PROVENANCE_SERVICE_PORT", 8002)
PROVENANCE_SERVICE_URL = os.getenv(
    "PROVENANCE_SERVICE_URL",
    f"http://{PROVENANCE_SERVICE_HOST}:{PROVENANCE_SERVICE_PORT}"
)
PROVENANCE_TIMEOUT = _env_int("PROVENANCE_TIMEOUT", 600)  # 10 minutes default

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
JOB_RETENTION_DAYS = _env_int("JOB_RETENTION_DAYS", 7)  # Days to retain job logs

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
MAX_IMAGE_PIXELS = _env_int("MAX_IMAGE_PIXELS", 200_000_000)

# Maximum number of files accepted by one batch image upload request
MAX_BATCH_UPLOAD_FILES = _env_int("MAX_BATCH_UPLOAD_FILES", 200)

# Password hashing settings
BCRYPT_ROUNDS = _env_int("BCRYPT_ROUNDS", 12)  # bcrypt cost factor (tests lower it for speed)

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