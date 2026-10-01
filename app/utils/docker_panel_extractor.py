"""
Docker-based panel extraction using panel-extractor container

Extracts individual panels from images using YOLO-based panel detection.
Outputs extracted panel images and a PANELS.csv file mapping panels to source images
with bounding box coordinates and classifications.
"""
import csv
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.config.settings import (
    PANEL_EXTRACTION_DOCKER_WORKDIR,
    PANEL_EXTRACTION_TIMEOUT,
    PANEL_EXTRACTOR_DOCKER_IMAGE,
    convert_host_path_to_container,
)
from app.utils.docker_runner import Mount, run_tool_container

logger = logging.getLogger(__name__)


def extract_panels_with_docker(
    image_ids: List[str],
    user_id: str,
    image_paths: List[str],
    docker_image: str = None
) -> Tuple[bool, str, Dict]:
    """Extract panels from images using the panel-extractor container.

    The user's images directory is mounted read-only as input and its
    ``panels`` subdirectory writable as output. The container writes panel
    images and a PANELS.csv file (FIGNAME, PANEL_ID, LABEL, X0, Y0, X1, Y1).

    Returns:
        Tuple of (success, status_message, output_info) where output_info has
        panels_count, panels_csv_path, output_dir and panels_data.

    Raises:
        DockerUnavailableError: Docker could not be reached (transient).
    """
    output_info: Dict = {}
    docker_image = docker_image or PANEL_EXTRACTOR_DOCKER_IMAGE

    if not image_paths:
        return False, "No image paths provided", output_info
    if len(image_paths) != len(image_ids):
        return False, f"Mismatch between image_ids ({len(image_ids)}) and image_paths ({len(image_paths)})", output_info
    for path in image_paths:
        if not os.path.exists(path):
            return False, f"Image file not found: {path}", output_info

    # All images live under /<workspace>/<user_id>/images/
    parts = Path(image_paths[0]).parts
    if "images" not in parts:
        return False, f"No 'images' directory found in path: {image_paths[0]}", output_info
    input_dir = str(Path(*parts[:parts.index("images") + 1]))
    for path in image_paths:
        if os.path.commonpath([input_dir, path]) != input_dir:
            return False, f"All images must be under the input directory {input_dir}. Got: {path}", output_info

    output_dir = os.path.join(input_dir, "panels")
    os.makedirs(output_dir, exist_ok=True)

    workdir = PANEL_EXTRACTION_DOCKER_WORKDIR
    relative_paths = [os.path.relpath(path, input_dir) for path in image_paths]
    args = ["--input-path", *[f"{workdir}/input/{rel}" for rel in relative_paths],
            "--output-path", f"{workdir}/output"]

    run = run_tool_container(
        docker_image,
        args,
        mounts=[Mount(input_dir, f"{workdir}/input"), Mount(output_dir, f"{workdir}/output", read_only=False)],
        timeout=PANEL_EXTRACTION_TIMEOUT,
        purpose="panels",
    )
    if run.stderr:
        logger.debug("panel-extractor stderr: %s", run.stderr[-2000:])

    panels_csv_path = os.path.join(output_dir, "PANELS.csv")
    if not run.ok or not os.path.exists(panels_csv_path):
        reason = run.describe_failure() if not run.ok else "no PANELS.csv produced"
        return False, f"Panel extraction failed ({reason})", output_info

    try:
        panels_data = _parse_panels_csv(panels_csv_path, image_paths, image_ids)
    except Exception as e:
        logger.error("Error parsing PANELS.csv: %s", e, exc_info=True)
        return False, f"Error parsing PANELS.csv: {e}", output_info

    output_info = {
        "panels_count": len(panels_data),
        "panels_csv_path": str(convert_host_path_to_container(panels_csv_path)),
        "output_dir": str(convert_host_path_to_container(output_dir)),
        "panels_data": panels_data,
        "status": "completed"
    }
    return True, f"Panel extraction successful for user_id={user_id}. Extracted {len(panels_data)} panels", output_info


def _parse_panels_csv(
    csv_path: str,
    image_paths: List[str],
    image_ids: List[str]
) -> List[Dict[str, Any]]:
    """Parse PANELS.csv and map FIGNAME to image IDs.

    Maps the FIGNAME column (source image basename) to the corresponding
    MongoDB image ID from the provided lists.

    Args:
        csv_path: Path to PANELS.csv file
        image_paths: List of image file paths
        image_ids: List of corresponding MongoDB image IDs

    Returns:
        List of panel dictionaries with parsed data:
        {
            "figname": str,
            "image_id": str,
            "panel_id": str,
            "panel_type": str,
            "bbox": {"x0": float, "y0": float, "x1": float, "y1": float}
        }

    Raises:
        ValueError: If FIGNAME cannot be matched to any image_id
        KeyError: If required columns are missing from CSV
    """
    # Build mapping from filename stem (without extension) to image_id
    # The CSV FIGNAME column contains stems like "1763554812_fig1"
    # but image filenames include extensions like "1763554812_fig1.jpg"
    filename_to_id = {}
    filename_stem_to_id = {}
    for path, img_id in zip(image_paths, image_ids):
        filename = os.path.basename(path)
        filename_to_id[filename] = img_id
        # Also add the stem (filename without extension) for matching FIGNAME
        filename_stem = os.path.splitext(filename)[0]
        filename_stem_to_id[filename_stem] = img_id

    logger.debug(f"Filename to image_id mapping: {filename_to_id}")
    logger.debug(f"Filename stem to image_id mapping: {filename_stem_to_id}")

    panels_data = []

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            raise ValueError("PANELS.csv is empty or has no header")

        # Validate required columns
        # Note: The actual CSV uses 'ID' not 'PANEL_ID', and 'LABEL' for panel type classification
        required_columns = {'FIGNAME', 'ID', 'LABEL', 'X0', 'Y0', 'X1', 'Y1'}
        if not required_columns.issubset(set(reader.fieldnames)):
            raise KeyError(f"PANELS.csv missing required columns. Expected: {required_columns}, Got: {set(reader.fieldnames)}")

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (after header)
            try:
                figname = row['FIGNAME'].strip()
                logger.debug(f"Row {row_num}: Processing FIGNAME='{figname}'")
                logger.debug(f"  Checking exact match in: {list(filename_to_id.keys())}")
                logger.debug(f"  Checking stem match in: {list(filename_stem_to_id.keys())}")

                # Map FIGNAME to image_id
                # First try exact match (with extension)
                image_id = filename_to_id.get(figname)
                logger.debug(f"  Exact match result: {image_id}")
                
                # If no exact match, try matching by stem (FIGNAME is usually just the stem)
                if not image_id:
                    image_id = filename_stem_to_id.get(figname)
                    logger.debug(f"  Stem match result: {image_id}")
                
                if not image_id:
                    raise ValueError(
                        f"Row {row_num}: FIGNAME '{figname}' not found in source images. "
                        f"Available: {list(filename_to_id.keys())}"
                    )

                # Parse bbox coordinates
                try:
                    bbox = {
                        "x0": float(row['X0'].strip()),
                        "y0": float(row['Y0'].strip()),
                        "x1": float(row['X1'].strip()),
                        "y1": float(row['Y1'].strip())
                    }
                except ValueError as e:
                    raise ValueError(f"Row {row_num}: Invalid bbox coordinates: {str(e)}")

                panel_data = {
                    "figname": figname,
                    "image_id": image_id,
                    "panel_id": row['ID'].strip(),
                    "panel_type": row['LABEL'].strip(),
                    "bbox": bbox
                }

                panels_data.append(panel_data)
                logger.debug(f"Row {row_num}: Parsed panel {panel_data['panel_id']} from {figname}")

            except (ValueError, KeyError) as e:
                logger.error(f"Error parsing row {row_num}: {str(e)}")
                raise

    logger.info(f"Successfully parsed {len(panels_data)} panels from PANELS.csv")
    return panels_data
