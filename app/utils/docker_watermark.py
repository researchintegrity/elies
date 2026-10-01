"""
Docker-based PDF watermark removal using the pdf-watermark-removal container
"""
import logging
import os
import shutil
import uuid
from typing import Dict, Optional, Tuple

from app.config.settings import (
    PDF_WATERMARK_REMOVAL_DOCKER_IMAGE,
    WATERMARK_REMOVAL_OUTPUT_SUFFIX_TEMPLATE,
    WATERMARK_REMOVAL_TIMEOUT,
    convert_host_path_to_container,
)
from app.utils.docker_runner import Mount, run_tool_container

logger = logging.getLogger(__name__)


def remove_watermark_with_docker(
    doc_id: str,
    user_id: str,
    pdf_file_path: str,
    aggressiveness_mode: int = 2,
    docker_image: str | None = None,
    output_id: Optional[str] = None,
) -> Tuple[bool, str, Dict]:
    """Remove watermarks from a PDF using the pdf-watermark-removal container.

    The original PDF's directory is mounted read-only; the tool writes into a
    private staging directory, and the cleaned PDF is then moved next to the
    original as ``<output_id>.pdf`` (the id of the document record that will
    reference it, so every run gets its own file) or, without ``output_id``,
    as ``<name>_watermark_removed_m<mode>.pdf``.

    Args:
        doc_id: Document ID for tracking
        user_id: User ID
        pdf_file_path: Path to the PDF
        aggressiveness_mode: 1 (explicit watermarks), 2 (text + repeated
            graphics, default) or 3 (all graphics)
        docker_image: Override the configured image
        output_id: Name the stored file after this id

    Returns:
        Tuple of (success, status_message, output_file_info) where
        output_file_info has filename, path (container path), size, status
        and aggressiveness_mode.

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    output_file_info: Dict = {}
    docker_image = docker_image or PDF_WATERMARK_REMOVAL_DOCKER_IMAGE

    if aggressiveness_mode not in (1, 2, 3):
        return False, f"Invalid aggressiveness mode: {aggressiveness_mode}. Must be 1, 2, or 3.", output_file_info
    if not os.path.exists(pdf_file_path):
        return False, f"PDF file not found: {pdf_file_path}", output_file_info

    pdf_file_path = os.path.abspath(pdf_file_path)
    pdf_dir = os.path.dirname(pdf_file_path)
    pdf_filename = os.path.basename(pdf_file_path)
    output_filename = (
        f"{os.path.splitext(pdf_filename)[0]}"
        f"{WATERMARK_REMOVAL_OUTPUT_SUFFIX_TEMPLATE.format(mode=aggressiveness_mode)}"
    )
    staging_dir = os.path.join(pdf_dir, ".watermark-staging", f"{doc_id}-{uuid.uuid4().hex[:8]}")
    os.makedirs(staging_dir)

    try:
        run = run_tool_container(
            docker_image,
            ["-i", f"/input/{pdf_filename}", "-o", f"/output/{output_filename}", "-m", str(aggressiveness_mode)],
            mounts=[Mount(pdf_dir, "/input"), Mount(staging_dir, "/output", read_only=False)],
            timeout=WATERMARK_REMOVAL_TIMEOUT,
            purpose="watermark",
        )
        staged_output = os.path.join(staging_dir, output_filename)
        if not run.ok or not os.path.exists(staged_output):
            reason = run.describe_failure() if not run.ok else "no output file produced"
            return False, f"Watermark removal failed ({reason})", output_file_info

        final_path = os.path.join(pdf_dir, f"{output_id}.pdf" if output_id else output_filename)
        shutil.move(staged_output, final_path)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    output_file_info = {
        "filename": output_filename,
        "path": str(convert_host_path_to_container(final_path)),
        "size": os.path.getsize(final_path),
        "status": "completed",
        "aggressiveness_mode": aggressiveness_mode
    }
    return True, f"Watermark removal successful for doc_id={doc_id}. Output: {output_filename}", output_file_info
