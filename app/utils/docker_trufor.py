"""
Docker-based TruFor Detection using the trufor container
"""
import logging
import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from app.config.settings import (
    TRUFOR_DOCKER_IMAGE,
    TRUFOR_TIMEOUT,
    TRUFOR_USE_GPU,
    convert_host_path_to_container,
)
from app.schemas import AnalysisType
from app.utils.docker_runner import Mount, run_tool_container
from app.utils.file_storage import get_analysis_output_path

logger = logging.getLogger(__name__)

STATUS_PREFIX = "[STATUS]"


def run_trufor_detection_with_docker(
    analysis_id: str,
    user_id: str,
    image_path: str,
    docker_image: str | None = None,
    save_noiseprint: bool = False,
    status_callback: Optional[Callable[[str], None]] = None
) -> Tuple[bool, str, Dict]:
    """Run TruFor detection on an image using Docker.

    The tool prints ``[STATUS] ...`` lines while it works; they are forwarded
    to ``status_callback`` as they arrive. The container is killed if it runs
    longer than TRUFOR_TIMEOUT (plus a short grace period).

    Returns:
        Tuple (success, message, results) with container paths of
        'pred_map', 'conf_map' and optionally 'noiseprint'.

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    results: Dict = {}
    docker_image = docker_image or TRUFOR_DOCKER_IMAGE

    if not os.path.exists(image_path):
        return False, f"Source image file not found: {image_path}", results

    image_path = os.path.abspath(image_path)
    image_filename = os.path.basename(image_path)
    try:
        output_dir = os.path.abspath(get_analysis_output_path(user_id, analysis_id, AnalysisType.TRUFOR))
    except OSError as e:
        return False, f"Failed to create output directory: {e}", results

    args = ["-gpu", "0" if TRUFOR_USE_GPU else "-1",
            "-in", f"/data/{image_filename}", "-out", "/data_out", "--timeout", str(TRUFOR_TIMEOUT)]
    if save_noiseprint:
        args.append("--save-noiseprint")

    def forward_status(line: str) -> None:
        line = line.strip()
        if line.startswith(STATUS_PREFIX) and status_callback:
            status_callback(line[len(STATUS_PREFIX):].strip())

    run = run_tool_container(
        docker_image,
        args,
        mounts=[Mount(os.path.dirname(image_path), "/data"), Mount(output_dir, "/data_out", read_only=False)],
        timeout=TRUFOR_TIMEOUT + 60,
        gpu=TRUFOR_USE_GPU,
        purpose="trufor",
        on_stdout_line=forward_status,
    )
    if not run.ok:
        if "Unknown runtime specified nvidia" in run.stderr:
            return False, "GPU runtime not available. Set TRUFOR_USE_GPU=false or install the NVIDIA runtime.", results
        return False, f"Detection failed ({run.describe_failure()})", results

    basename = os.path.splitext(image_filename)[0]
    for key in ("pred_map", "conf_map", "noiseprint"):
        path = os.path.join(output_dir, f"{basename}_{key}.png")
        if os.path.exists(path):
            results[key] = str(convert_host_path_to_container(Path(path)))

    if "pred_map" in results and "conf_map" in results:
        return True, "Analysis completed successfully", results
    if results:
        logger.warning("Partial TruFor output for %s: %s", analysis_id, sorted(results))
        return True, "Analysis completed with partial results", results

    files = os.listdir(output_dir)
    if files:
        logger.warning("TruFor output names did not match the expected pattern: %s", files)
        results["files"] = [str(convert_host_path_to_container(Path(output_dir) / f)) for f in files]
        return True, "Analysis completed (filename mismatch?)", results
    return False, "Analysis completed but no output file found.", results
