"""
Forgeryscope copy-move detection against the real tool image (#80).

Needs a Docker daemon, the forgeryscope image
(docker compose --profile tools build forgeryscope) and the
system_modules/forgeryscope submodule, whose example figures are analysed.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config.settings import FORGERYSCOPE_DOCKER_IMAGE, UPLOAD_DIR
from app.utils.docker_forgeryscope import run_forgeryscope_with_docker

SAMPLES = Path(__file__).resolve().parents[1] / "system_modules" / "forgeryscope" / "examples" / "sample_data"


def _image_available() -> bool:
    try:
        inspect = subprocess.run(["docker", "image", "inspect", FORGERYSCOPE_DOCKER_IMAGE],
                                 capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return inspect.returncode == 0


pytestmark = pytest.mark.skipif(not (SAMPLES.is_dir() and _image_available()),
                                reason="forgeryscope image or example figures not available")


def _uploaded(name: str) -> str:
    target = UPLOAD_DIR / "e2e-user" / "images" / "uploaded" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SAMPLES / name, target)
    return str(target)


def test_duplicated_blots_are_found():
    statuses = []

    ok, message, results = run_forgeryscope_with_docker(
        "e2e-duplicated", "single_image_copy_move", "e2e-user", _uploaded("kaggle_test_img_45.png"),
        status_callback=statuses.append)

    assert ok, message
    assert results["verdict"] == "duplicated"
    assert any(d["panel_type"] == "Blots" for d in results["detections"])
    assert Path(results["matches_image"]).is_file() and Path(results["clusters_image"]).is_file()
    assert Path(results["report"]).is_file()
    assert "Detecting panels" in statuses


def test_authentic_figure():
    ok, message, results = run_forgeryscope_with_docker(
        "e2e-authentic", "single_image_copy_move", "e2e-user", _uploaded("wblot_sample.png"))

    assert ok, message
    assert results["verdict"] == "authentic" and results["detections"] == []
    assert "matches_image" not in results
