"""
File storage utilities for document and image upload handling
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Import storage configuration
from app.config.storage_quota import MAX_PDF_FILE_SIZE, MAX_IMAGE_FILE_SIZE, DEFAULT_USER_STORAGE_QUOTA
from app.config.settings import (
    PDF_EXTRACTOR_DOCKER_IMAGE, 
    UPLOAD_DIR,
)

# File size limits (in bytes) - imported from config
MAX_PDF_SIZE = MAX_PDF_FILE_SIZE
MAX_IMAGE_SIZE = MAX_IMAGE_FILE_SIZE

# Allowed file extensions
ALLOWED_PDF_EXTENSIONS = {".pdf"}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def ensure_directories_exist():
    """Ensure all required directories exist"""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_user_upload_path(user_id: str, subfolder: str = None) -> Path:
    """
    Get the upload path for a specific user
    
    Args:
        user_id: User ID
        subfolder: Optional subfolder (e.g., 'pdfs', 'images')
        
    Returns:
        Path object for user's upload directory
    """
    user_path = UPLOAD_DIR / user_id
    
    if subfolder:
        user_path = user_path / subfolder
    
    logger.debug("Ensuring directory exists: %s", user_path)
    try:
        user_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to create directory {user_path}: {e}")
        # Check if it exists and what it is
        if user_path.exists():
            logger.error(f"Path exists. Is dir? {user_path.is_dir()}. Is file? {user_path.is_file()}")
        raise
    return user_path


def get_extraction_output_path(user_id: str, doc_id: str) -> Path:
    """
    Get the path where extracted images should be saved for a document
    
    Args:
        user_id: User ID
        doc_id: Document ID
        
    Returns:
        Path object for extracted images directory
    """
    extraction_path = UPLOAD_DIR / user_id / "images" / "extracted" / doc_id
    extraction_path.mkdir(parents=True, exist_ok=True)
    return extraction_path


def get_panel_output_path(user_id: str, doc_id: str = None) -> Path:
    """
    Get the path where extracted panels should be saved
    
    Panels are extracted from images via Docker and saved to:
    /workspace/{user_id}/images/panels/ or /workspace/{user_id}/images/panels/{doc_id}/
    
    Args:
        user_id: User ID
        doc_id: Optional document ID (for organizing panels by source)
        
    Returns:
        Path object for panels directory
    """
    if doc_id:
        panels_path = UPLOAD_DIR / user_id / "images" / "panels" / doc_id
    else:
        panels_path = UPLOAD_DIR / user_id / "images" / "panels"
    
    panels_path.mkdir(parents=True, exist_ok=True)
    return panels_path


def get_thumbnail_path(user_id: str, image_id: str) -> Path:
    """
    Get the path for a generated thumbnail
    
    Thumbnails are saved to:
    /workspace/{user_id}/images/thumbnails/{image_id}.jpg
    
    Args:
        user_id: User ID
        image_id: Image ID
        
    Returns:
        Path object for thumbnail file
    """
    thumb_dir = UPLOAD_DIR / user_id / "images" / "thumbnails"
    thumb_dir.mkdir(parents=True, exist_ok=True)
    return thumb_dir / f"{image_id}.jpg"


def get_analysis_output_path(user_id: str, analysis_id: str, analysis_type: str) -> Path:
    """
    Get the path where analysis results should be saved
    
    Results are saved to:
    <workspace-env>/{user_id}/analyses/{analysis_type}/{analysis_id}/
    
    Args:
        user_id: User ID
        analysis_id: Analysis ID
        analysis_type: Type of analysis (e.g., 'single_image_copy_move', 'cross_image_copy_move', 'screening_tool')
        
    Returns:
        Path object for analysis directory
    """
    # Map analysis types to folder names if needed, or use type directly
    if analysis_type == "single_image_copy_move":
        folder_name = "cmfd"
    elif analysis_type == "cross_image_copy_move":
        folder_name = "cmfd_cross"
    elif analysis_type == "trufor":
        folder_name = "trufor"
    elif analysis_type == "screening_tool":
        folder_name = "screening_tool"  # Client-side screening tool results (ELA, noise, etc.)
    else:
        folder_name = analysis_type
    
    analysis_path = UPLOAD_DIR / user_id / "analyses" / folder_name / analysis_id
    analysis_path.mkdir(parents=True, exist_ok=True)
    return analysis_path

def delete_file(file_path: str) -> Tuple[bool, Optional[str]]:
    """
    Delete a file from disk
    
    Args:
        file_path: Path to file
        
    Returns:
        Tuple of (success, error_message)
    """
    try:
        path = Path(file_path)
        
        if path.exists():
            path.unlink()
            return True, None
        else:
            # Log the path we tried to delete for debugging
            logger.warning(f"File not found for deletion: {path} (original: {file_path})")
            return False, "File not found."
    except Exception as e:
        return False, f"Failed to delete file: {str(e)}"


def delete_directory(dir_path: str) -> Tuple[bool, Optional[str]]:
    """
    Delete a directory and all its contents
    
    Args:
        dir_path: Path to directory
        
    Returns:
        Tuple of (success, error_message)
    """
    try:
        path = Path(dir_path)
        if path.exists() and path.is_dir():
            shutil.rmtree(path)
            return True, None
        else:
            logger.warning(f"Directory not found for deletion: {path} (original: {dir_path})")
            return False, "Directory not found."
    except Exception as e:
        return False, f"Failed to delete directory: {str(e)}"


# ============================================================================
# Storage Quota Management
# ============================================================================

def get_user_storage_usage(user_id: str) -> int:
    """
    Calculate total storage used by a user across all files
    
    Recursively sums the size of all files in the user's upload directory
    (PDFs, uploaded images, and extracted images)
    
    Args:
        user_id: User ID
        
    Returns:
        Total storage used in bytes
    """
    user_dir = UPLOAD_DIR / user_id
    
    if not user_dir.exists():
        return 0
    
    total_size = 0
    try:
        # Walk through all files in user directory and sum sizes
        for file_path in user_dir.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size
    except Exception as e:
        # Log but don't raise - return what we can
        logger.warning(f"Error calculating storage for user {user_id}: {str(e)}")
    
    return total_size


def check_storage_quota(user_id: str, file_size: int, quota_bytes: int = None) -> Tuple[bool, Optional[str]]:
    """
    Check if adding a file would exceed storage quota
    
    Args:
        user_id: User ID
        file_size: Size of file being uploaded in bytes
        quota_bytes: User's storage quota (defaults to DEFAULT_USER_STORAGE_QUOTA)
        
    Returns:
        Tuple of (quota_available, error_message)
        - quota_available: True if file can be uploaded, False otherwise
        - error_message: Description of quota issue or None if OK
    """
    if quota_bytes is None:
        quota_bytes = DEFAULT_USER_STORAGE_QUOTA
    
    current_usage = get_user_storage_usage(user_id)
    remaining = quota_bytes - current_usage
    
    if remaining < file_size:
        from app.config.storage_quota import format_bytes
        return False, (
            f"Storage quota exceeded. File size: {format_bytes(file_size)}, "
            f"Remaining quota: {format_bytes(remaining)}. "
            f"Total quota: {format_bytes(quota_bytes)}"
        )
    
    return True, None


def get_quota_status(user_id: str, quota_bytes: int = None) -> dict:
    """
    Get detailed storage quota status for a user
    
    Args:
        user_id: User ID
        quota_bytes: User's storage quota (defaults to DEFAULT_USER_STORAGE_QUOTA)
        
    Returns:
        Dictionary with quota information:
        {
            "used_bytes": int,
            "quota_bytes": int,
            "remaining_bytes": int,
            "used_percentage": float
        }
    """
    if quota_bytes is None:
        quota_bytes = DEFAULT_USER_STORAGE_QUOTA
    
    used_bytes = get_user_storage_usage(user_id)
    remaining_bytes = max(0, quota_bytes - used_bytes)
    used_percentage = (used_bytes / quota_bytes * 100) if quota_bytes > 0 else 0
    
    return {
        "used_bytes": used_bytes,
        "quota_bytes": quota_bytes,
        "remaining_bytes": remaining_bytes,
        "used_percentage": round(used_percentage, 2)
    }


# ============================================================================
# Figure Extraction Placeholder
# ============================================================================

def figure_extraction_hook(
    doc_id: str,
    user_id: str,
    pdf_file_path: str
) -> Tuple[int, list[str], list]:
    """
    Extract figures from PDF using Docker container
    
    This function is called automatically after a PDF is uploaded.
    It uses the pdf-extractor Docker container to extract images from the PDF
    and saves them to: /workspace/{user_id}/images/extracted/{doc_id}/
    
    Docker Integration:
        Uses docker run with volume mounting to process PDFs safely in container:
        - Input volume: {pdf_directory}:/INPUT
        - Output volume: {output_directory}:/OUTPUT
        - Environment: INPUT_PATH=/INPUT/{filename}, OUTPUT_PATH=/OUTPUT
        - Image: pdf-extractor:latest
    
    Args:
        doc_id: Document ID
        user_id: User ID
        pdf_file_path: Path to the PDF file
        
    Returns:
        Tuple of (extracted_image_count, extraction_errors, extracted_files)
        - extracted_image_count: Number of images successfully extracted
        - extraction_errors: List of error messages encountered during extraction
        - extracted_files: List of dicts with extracted image metadata
        
    Raises:
        Should NOT raise exceptions. Instead, return errors in the list.
    """
    from app.utils.docker_extraction import extract_images_with_docker
    
    try:
        # Use Docker container for extraction
        extracted_count, extraction_errors, extracted_files = extract_images_with_docker(
            doc_id=doc_id,
            user_id=user_id,
            pdf_file_path=pdf_file_path,
            docker_image=PDF_EXTRACTOR_DOCKER_IMAGE
        )
        
        if extracted_count > 0:
            logger.debug(f"Extracted {extracted_count} images for doc_id={doc_id}")
        elif extraction_errors:
            logger.warning(f"Extraction errors for doc_id={doc_id}: {extraction_errors}")
        
        return extracted_count, extraction_errors, extracted_files
    
    except Exception as e:
        # Don't raise - return as error in list
        error_msg = f"Extraction failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return 0, [error_msg], []


# ============================================================================
# User Storage Tracking
# ============================================================================

def update_user_storage_in_db(user_id: str) -> int:
    """
    Update the storage_used_bytes field in the users collection
    
    Calculates current storage usage and updates the user document
    in MongoDB for easy access without recalculating each time.
    
    Args:
        user_id: User ID
        
    Returns:
        Updated storage usage in bytes
    """
    from app.db.mongodb import get_users_collection
    from bson import ObjectId
    
    # Calculate current usage
    current_usage = get_user_storage_usage(user_id)
    
    # Update user document
    try:
        users_col = get_users_collection()
        users_col.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "storage_used_bytes": current_usage,
                    "updated_at": datetime.now(timezone.utc)
                }
            }
        )
    except Exception as e:
        # Log but don't raise - storage tracking should not block operations
        logger.warning(f"Failed to update storage_used_bytes for user {user_id}: {str(e)}")
    
    return current_usage


# Initialize directories on module load
ensure_directories_exist()
