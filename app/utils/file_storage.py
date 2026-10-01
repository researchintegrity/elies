"""
File storage utilities for document and image upload handling
"""
import logging
from pathlib import Path
from typing import Tuple

from app.config.settings import PDF_EXTRACTOR_DOCKER_IMAGE, UPLOAD_DIR
from app.config.storage_quota import MAX_IMAGE_FILE_SIZE, MAX_PDF_FILE_SIZE
from app.exceptions import TransientError

logger = logging.getLogger(__name__)

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


_ANALYSIS_FOLDERS = {
    "single_image_copy_move": "cmfd",
    "cross_image_copy_move": "cmfd_cross",
    "trufor": "trufor",
    "screening_tool": "screening_tool",  # Client-side screening tool results (ELA, noise, etc.)
}


def analysis_output_dir(user_id: str, analysis_id: str, analysis_type: str) -> Path:
    """
    Where an analysis stores its result files (the directory is not created):
    <workspace>/{user_id}/analyses/{type folder}/{analysis_id}/
    """
    analysis_type = getattr(analysis_type, "value", analysis_type)
    folder_name = _ANALYSIS_FOLDERS.get(analysis_type, analysis_type)
    return UPLOAD_DIR / user_id / "analyses" / folder_name / analysis_id


def get_analysis_output_path(user_id: str, analysis_id: str, analysis_type: str) -> Path:
    """
    Get (and create) the directory where analysis results should be saved.

    Args:
        user_id: User ID
        analysis_id: Analysis ID
        analysis_type: Type of analysis (e.g., 'single_image_copy_move', 'cross_image_copy_move', 'screening_tool')

    Returns:
        Path object for analysis directory
    """
    analysis_path = analysis_output_dir(user_id, analysis_id, analysis_type)
    analysis_path.mkdir(parents=True, exist_ok=True)
    return analysis_path


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

    except TransientError:
        # Docker unreachable: let the task retry later
        raise
    except Exception as e:
        # Don't raise - return as error in list
        error_msg = f"Extraction failed: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return 0, [error_msg], []


# Initialize directories on module load
ensure_directories_exist()
