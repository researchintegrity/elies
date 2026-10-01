"""
Docker-based PDF image extraction using the pdf-extractor container
"""
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple

from app.config.settings import (
    DOCKER_EXTRACTION_TIMEOUT,
    IMAGE_MIME_TYPES,
    PDF_EXTRACTOR_DOCKER_IMAGE,
    SUPPORTED_IMAGE_EXTENSIONS,
)
from app.utils.docker_runner import Mount, run_tool_container

logger = logging.getLogger(__name__)


def extract_images_with_docker(
    doc_id: str,
    user_id: str,
    pdf_file_path: str,
    docker_image: str = None
) -> Tuple[int, List[str], List[Dict]]:
    """
    Extract images from a PDF with the pdf-extractor container.

    The PDF's directory is mounted read-only at /INPUT and the document's
    extraction directory writable at /OUTPUT.

    Returns:
        Tuple of (extracted_image_count, extraction_errors, extracted_files)
        - extracted_files: dicts with {filename, path, size, mime_type}
        A PDF without images yields (0, [], []).

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    if docker_image is None:
        docker_image = PDF_EXTRACTOR_DOCKER_IMAGE

    if not os.path.exists(pdf_file_path):
        error_msg = f"PDF file not found: {pdf_file_path}"
        logger.error(error_msg)
        return 0, [error_msg], []

    from app.utils.file_storage import get_extraction_output_path

    pdf_file_path = os.path.abspath(pdf_file_path)
    output_dir = os.path.abspath(get_extraction_output_path(user_id, doc_id))
    pdf_filename = os.path.basename(pdf_file_path)

    run = run_tool_container(
        docker_image,
        mounts=[
            Mount(os.path.dirname(pdf_file_path), "/INPUT"),
            Mount(output_dir, "/OUTPUT", read_only=False),
        ],
        env={"INPUT_PATH": f"/INPUT/{pdf_filename}", "OUTPUT_PATH": "/OUTPUT"},
        timeout=DOCKER_EXTRACTION_TIMEOUT,
        purpose="pdf-extract",
    )
    if not run.ok:
        error_msg = f"PDF image extraction failed ({run.describe_failure()})"
        logger.error("%s for doc_id=%s", error_msg, doc_id)
        return 0, [error_msg], []

    extracted_files = []
    for filename in sorted(os.listdir(output_dir)):
        if not filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
            continue
        filepath = os.path.join(output_dir, filename)
        extracted_files.append({
            "filename": filename,
            "path": filepath,
            "size": os.path.getsize(filepath),
            "mime_type": IMAGE_MIME_TYPES.get(Path(filename).suffix.lower(), "image/unknown"),
        })

    logger.info("Extracted %d images for doc_id=%s", len(extracted_files), doc_id)
    return len(extracted_files), [], extracted_files
