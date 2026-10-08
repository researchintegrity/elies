"""
Docker-based copy-move detection with Forgeryscope (forgeryscope container).

Forgeryscope looks for duplicated content inside one figure: it detects the
blot and microscopy panels, selects candidate panel pairs by embedding
similarity, locates the duplicated regions with LightGlue keypoint matching,
and compares individual western blot lanes. It writes a JSON report, plus a
matches and a clusters image when duplication is found.
"""
import json
import logging
import os
from typing import Callable, Dict, Optional, Tuple

from app.config.settings import (
    FORGERYSCOPE_DOCKER_IMAGE,
    FORGERYSCOPE_TIMEOUT,
    FORGERYSCOPE_USE_GPU,
    convert_host_path_to_container,
)
from app.utils.docker_runner import Mount, run_tool_container
from app.utils.file_storage import get_analysis_output_path

logger = logging.getLogger(__name__)

STATUS_PREFIX = "[STATUS]"
ERROR_PREFIX = "[ERROR]"


def run_forgeryscope_with_docker(
    analysis_id: str,
    analysis_type: str,
    user_id: str,
    image_path: str,
    status_callback: Optional[Callable[[str], None]] = None,
    docker_image: str | None = None,
) -> Tuple[bool, str, Dict]:
    """Run Forgeryscope on an image using Docker.

    The tool prints ``[STATUS] ...`` lines while it works; they are forwarded
    to ``status_callback``. The input image is mounted read-only; results go
    to the analysis directory.

    Returns:
        Tuple (success, message, results). results holds 'verdict'
        ('authentic' or 'duplicated'), 'detections', 'panels', the container
        path of the JSON 'report' and, when duplication is found, of the
        'matches_image' and 'clusters_image'.

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    if not os.path.exists(image_path):
        return False, f"Source image file not found: {image_path}", {}

    image_path = os.path.abspath(image_path)
    image_filename = os.path.basename(image_path)
    base_name = os.path.splitext(image_filename)[0]
    output_dir = os.path.abspath(get_analysis_output_path(user_id, analysis_id, analysis_type))
    report_path = os.path.join(output_dir, f"{base_name}_result.json")

    # A retried task must not pick up the outputs of an earlier attempt
    for suffix in ("_result.json", "_matches.png", "_clusters.png"):
        try:
            os.remove(os.path.join(output_dir, f"{base_name}{suffix}"))
        except FileNotFoundError:
            pass

    args = [
        "--input", f"/input_vol/{image_filename}",
        "--output", "/output_vol",
        "--device", "cuda" if FORGERYSCOPE_USE_GPU else "cpu",
        "--timeout", str(FORGERYSCOPE_TIMEOUT),
    ]

    def forward_status(line: str) -> None:
        line = line.strip()
        if line.startswith(STATUS_PREFIX) and status_callback:
            status_callback(line[len(STATUS_PREFIX):].strip())

    # The tool stops itself at FORGERYSCOPE_TIMEOUT; the grace period covers
    # container start-up and model loading before its timer starts.
    run = run_tool_container(
        docker_image or FORGERYSCOPE_DOCKER_IMAGE,
        args,
        mounts=[Mount(os.path.dirname(image_path), "/input_vol"), Mount(output_dir, "/output_vol", read_only=False)],
        timeout=FORGERYSCOPE_TIMEOUT + 60,
        gpu=FORGERYSCOPE_USE_GPU,
        purpose="forgeryscope",
        on_stdout_line=forward_status,
    )
    if not run.ok:
        if "Unknown runtime specified nvidia" in run.stderr:
            return False, "GPU runtime not available. Set FORGERYSCOPE_USE_GPU=false or install the NVIDIA runtime.", {}
        # The tool's own one-line error rather than its traceback (the runner logs the whole output)
        errors = [line[len(ERROR_PREFIX):].strip() for line in run.stderr.splitlines() if line.startswith(ERROR_PREFIX)]
        if errors and not run.timed_out:
            return False, errors[-1], {}
        return False, f"Detection failed ({run.describe_failure()})", {}

    try:
        with open(report_path) as handle:
            report = json.load(handle)
    except (OSError, ValueError) as e:
        logger.error("Forgeryscope report %s unreadable: %s", report_path, e)
        return False, "Analysis completed but its report is missing or unreadable.", {}

    results: Dict = {
        "verdict": report.get("verdict"),
        "detections": report.get("detections") or [],
        "panels": report.get("panels") or [],
        "report": str(convert_host_path_to_container(report_path)),
    }
    for key, name in (report.get("outputs") or {}).items():
        if key in ("matches_image", "clusters_image") and name:
            # Only file names are expected: never follow a path out of the output directory
            path = os.path.join(output_dir, os.path.basename(name))
            if os.path.exists(path):
                results[key] = str(convert_host_path_to_container(path))
    # Without its images a positive result would look like "nothing found" in the UI
    if results["verdict"] == "duplicated" and "matches_image" not in results:
        return False, "Analysis found duplication but its result images are missing.", {}
    return True, "Analysis completed successfully", results
