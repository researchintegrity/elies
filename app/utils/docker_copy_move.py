"""
Docker-based Copy-Move Detection supporting both Dense and Keypoint methods.

Dense Method: Uses block-matching algorithm (copy-move-detection container)
Keypoint Method: Uses keypoint-based matching (copy-move-detection-keypoint container)
"""
import logging
import os
from typing import Dict, List, Tuple

from app.config.settings import (
    COPY_MOVE_DETECTION_DOCKER_IMAGE,
    COPY_MOVE_DETECTION_TIMEOUT,
    COPY_MOVE_KEYPOINT_DOCKER_IMAGE,
    COPY_MOVE_KEYPOINT_TIMEOUT,
    convert_host_path_to_container,
)
from app.utils.docker_runner import Mount, run_tool_container
from app.utils.file_storage import get_analysis_output_path

logger = logging.getLogger(__name__)

# Detection method constants
METHOD_DENSE = "dense"
METHOD_KEYPOINT = "keypoint"
VALID_DESCRIPTORS = ("cv_sift", "cv_rsift", "vlfeat_sift_heq")


def run_copy_move_detection_with_docker(
    analysis_id: str,
    analysis_type: str,
    user_id: str,
    image_path: str,
    target_image_path: str | None = None,
    method: str = METHOD_KEYPOINT,
    dense_method: int = 2,
    descriptor: str = "cv_rsift",
    docker_image: str | None = None
) -> Tuple[bool, str, Dict]:
    """Run copy-move detection on an image (or image pair) using Docker.

    Supports two detection methods:
    - 'keypoint': keypoint-based detection (recommended for cross-image);
      descriptors cv_sift, cv_rsift (default), vlfeat_sift_heq
    - 'dense': block-based dense matching (variants 1-5)

    Input images are mounted read-only; results go to the analysis directory.

    Returns:
        Tuple (success, message, results) where results holds container
        paths of the generated 'matches_image' / 'clusters_image'.

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    if method not in (METHOD_DENSE, METHOD_KEYPOINT):
        return False, f"Invalid method '{method}'. Must be '{METHOD_DENSE}' or '{METHOD_KEYPOINT}'", {}
    if method == METHOD_KEYPOINT and descriptor not in VALID_DESCRIPTORS:
        return False, f"Invalid descriptor '{descriptor}'. Must be one of: {', '.join(VALID_DESCRIPTORS)}", {}

    if not os.path.exists(image_path):
        return False, f"Source image file not found: {image_path}", {}
    if target_image_path and not os.path.exists(target_image_path):
        return False, f"Target image file not found: {target_image_path}", {}

    image_path = os.path.abspath(image_path)
    output_dir = os.path.abspath(get_analysis_output_path(user_id, analysis_id, analysis_type))

    mounts = [
        Mount(os.path.dirname(image_path), "/input_vol"),
        Mount(output_dir, "/output_vol", read_only=False),
    ]
    inputs: List[str] = [f"/input_vol/{os.path.basename(image_path)}"]
    if target_image_path:
        target_image_path = os.path.abspath(target_image_path)
        mounts.append(Mount(os.path.dirname(target_image_path), "/target_vol"))
        inputs.append(f"/target_vol/{os.path.basename(target_image_path)}")

    if docker_image is None and method == METHOD_KEYPOINT:
        image, timeout = COPY_MOVE_KEYPOINT_DOCKER_IMAGE, COPY_MOVE_KEYPOINT_TIMEOUT
        method_args = ["--descriptor", descriptor]
    else:
        image, timeout = docker_image or COPY_MOVE_DETECTION_DOCKER_IMAGE, COPY_MOVE_DETECTION_TIMEOUT
        method_args = ["--method", str(dense_method)]

    args = ["--input", *inputs, "--output", "/output_vol", *method_args, "--timeout", str(timeout)]
    # Allow the tool's own timeout to fire first, then enforce ours
    run = run_tool_container(image, args, mounts, timeout=timeout + 30, purpose="copy-move")
    if not run.ok:
        return False, f"Detection failed ({run.describe_failure()})", {}

    source_base = os.path.splitext(os.path.basename(image_path))[0]
    if target_image_path:
        target_base = os.path.splitext(os.path.basename(target_image_path))[0]
        base_name = f"{source_base}_vs_{target_base}"
    else:
        base_name = source_base

    results = {}
    for key, suffix in (("matches_image", "_matches.png"), ("clusters_image", "_clusters.png")):
        path = os.path.join(output_dir, f"{base_name}{suffix}")
        if os.path.exists(path):
            results[key] = str(convert_host_path_to_container(path))

    if not results:
        return False, "Analysis completed but no output files were found.", results
    return True, "Analysis completed successfully", results
