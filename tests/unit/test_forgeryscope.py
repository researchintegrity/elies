"""Forgeryscope copy-move detection: runner, task, routes and downloads (#80)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bson import ObjectId

import app.tasks.copy_move_detection as copy_move_task
import app.utils.docker_forgeryscope as forgeryscope
from app.config.settings import UPLOAD_DIR
from app.db.mongodb import get_analyses_collection, get_jobs_collection
from app.utils.docker_runner import ContainerRun
from tests.unit.conftest import PNG_BYTES
from tests.unit.test_task_lifecycle import _run

DETECTION = {
    "id": 1, "colour": "#e6194b", "level": "panel", "panel_type": "Blots", "panel_ids": [3, 4],
    "similarity": 0.97, "bbox": [305, 286, 792, 1421], "area": 85536, "pairs": [], "lane_matches": [],
}
PANELS = [{"id": 3, "label": "Blots", "confidence": 0.93, "bbox": [305.9, 1321.8, 791.9, 1421.3]}]


class FakeForgeryscope:
    """Stands in for run_tool_container and writes what the tool would write."""

    def __init__(self, verdict="duplicated", images=True, report=True, returncode=0, stdout_lines=(), stderr="boom"):
        self.verdict = verdict
        self.stderr = stderr
        self.images = images
        self.report = report
        self.returncode = returncode
        self.stdout_lines = stdout_lines
        self.calls = []

    def __call__(self, image, args=(), mounts=(), *, timeout, env=None, gpu=False, purpose="tool", on_stdout_line=None):
        args = list(args)
        self.calls.append({"image": image, "args": args, "mounts": {m.target: m for m in mounts}, "gpu": gpu,
                           "timeout": timeout})
        out = Path(next(m.source for m in mounts if m.target == "/output_vol"))
        base = Path(args[args.index("--input") + 1]).stem
        outputs = {"matches_image": None, "clusters_image": None}
        if self.images:
            outputs = {"matches_image": f"{base}_matches.png", "clusters_image": f"{base}_clusters.png"}
            for name in outputs.values():
                (out / name).write_bytes(PNG_BYTES)
        if self.report:
            detections = [DETECTION] if self.verdict == "duplicated" else []
            (out / f"{base}_result.json").write_text(json.dumps(
                {"verdict": self.verdict, "detections": detections, "panels": PANELS, "outputs": outputs}))
        for line in self.stdout_lines:
            on_stdout_line(line)
        return ContainerRun("elies-fake", self.returncode, "", self.stderr if self.returncode else "", False, 0.1)


@pytest.fixture
def image_file():
    path = UPLOAD_DIR / "u1" / "images" / "uploaded" / "fig.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES)
    return str(path)


def _detect(image_file, **kwargs):
    return forgeryscope.run_forgeryscope_with_docker("an1", "single_image_copy_move", "u1", image_file, **kwargs)


# ------------------------------------------------------------ runner ----

def test_duplication_found(monkeypatch, image_file):
    fake = FakeForgeryscope(stdout_lines=["loading", "[STATUS] Detecting panels", "[STATUS] Done"])
    monkeypatch.setattr(forgeryscope, "run_tool_container", fake)
    statuses = []

    ok, message, results = _detect(image_file, status_callback=statuses.append)

    assert ok
    assert results["verdict"] == "duplicated"
    assert results["detections"] == [DETECTION] and results["panels"] == PANELS
    assert results["matches_image"].endswith("fig_matches.png")
    assert results["clusters_image"].endswith("fig_clusters.png")
    assert results["report"].endswith("fig_result.json")
    assert statuses == ["Detecting panels", "Done"]

    call = fake.calls[0]
    assert call["image"] == "forgeryscope:latest"
    assert call["args"][:4] == ["--input", "/input_vol/fig.png", "--output", "/output_vol"]
    assert call["args"][call["args"].index("--device") + 1] == "cpu" and not call["gpu"]
    assert call["mounts"]["/input_vol"].read_only and not call["mounts"]["/output_vol"].read_only


def test_authentic_figure_has_no_images(monkeypatch, image_file):
    monkeypatch.setattr(forgeryscope, "run_tool_container", FakeForgeryscope(verdict="authentic", images=False))

    ok, message, results = _detect(image_file)

    assert ok and results["verdict"] == "authentic" and results["detections"] == []
    assert "matches_image" not in results and "clusters_image" not in results


@pytest.mark.parametrize("fake, error", [
    (FakeForgeryscope(returncode=2), "Detection failed (exit code 2: boom)"),
    (FakeForgeryscope(returncode=1, stderr="Traceback (most recent call last):\n  ...\n[ERROR] Image too large: 9x9 pixels"),
     "Image too large: 9x9 pixels"),
    (FakeForgeryscope(report=False), "report is missing"),
    (FakeForgeryscope(images=False), "result images are missing"),
])
def test_failures(monkeypatch, image_file, fake, error):
    monkeypatch.setattr(forgeryscope, "run_tool_container", fake)

    ok, message, results = _detect(image_file)

    assert not ok and error in message and "Traceback" not in message and results == {}


def test_gpu(monkeypatch, image_file):
    fake = FakeForgeryscope()
    monkeypatch.setattr(forgeryscope, "run_tool_container", fake)
    monkeypatch.setattr(forgeryscope, "FORGERYSCOPE_USE_GPU", True)

    assert _detect(image_file)[0]
    args = fake.calls[0]["args"]
    assert fake.calls[0]["gpu"] and args[args.index("--device") + 1] == "cuda"


def test_missing_image():
    ok, message, results = _detect(str(UPLOAD_DIR / "u1" / "nope.png"))
    assert not ok and "not found" in message


# ------------------------------------------------------- task, routes ----

def _upload(client, user):
    return client.post("/images/upload", headers=user.headers,
                       files={"file": ("a.png", PNG_BYTES, "image/png")}).json()["_id"]


def test_route_queues_forgeryscope(client, alice, celery_calls):
    image_id = _upload(client, alice)

    response = client.post("/analyses/copy-move/single", headers=alice.headers,
                           json={"image_id": image_id, "method": "forgeryscope"})

    assert response.status_code == 202
    analysis = get_analyses_collection().find_one({"_id": ObjectId(response.json()["analysis_id"])})
    assert analysis["type"] == "single_image_copy_move"
    assert analysis["parameters"] == {"method": "forgeryscope", "dense_method": None}
    call = next(c for c in celery_calls if c.name == "tasks.detect_copy_move")
    assert call.kwargs["method"] == "forgeryscope"
    assert get_jobs_collection().find_one({"job_type": "copy_move_single"})["title"] == \
        "Copy-Move Detection (Forgeryscope)"


def test_existing_requests_are_unchanged(client, alice, celery_calls):
    image_id = _upload(client, alice)

    response = client.post("/analyses/copy-move/single", headers=alice.headers, json={"image_id": image_id})

    assert response.status_code == 202
    call = next(c for c in celery_calls if c.name == "tasks.detect_copy_move")
    assert call.kwargs["method"] == "dense" and call.kwargs["dense_method"] == 2


def test_cross_image_rejects_forgeryscope(client, alice):
    response = client.post("/analyses/copy-move/cross", headers=alice.headers, json={
        "source_image_id": _upload(client, alice), "target_image_id": _upload(client, alice),
        "method": "forgeryscope"})
    assert response.status_code == 422


def test_task_stores_detections(client, alice, monkeypatch):
    image_id = _upload(client, alice)
    analysis_id = client.post("/analyses/copy-move/single", headers=alice.headers,
                              json={"image_id": image_id, "method": "forgeryscope"}).json()["analysis_id"]
    job = get_jobs_collection().find_one({"job_type": "copy_move_single"})
    statuses = []

    def detect(**kwargs):
        kwargs["status_callback"]("Matching 1 candidate panel pairs")
        statuses.append(get_jobs_collection().find_one({"_id": job["_id"]}).get("current_step"))
        return True, "ok", {"verdict": "duplicated", "detections": [DETECTION], "panels": PANELS,
                            "matches_image": "/m.png", "clusters_image": "/c.png", "report": "/r.json"}

    monkeypatch.setattr(copy_move_task, "run_forgeryscope_with_docker", detect)
    _run(copy_move_task.detect_copy_move, analysis_id=analysis_id, image_id=image_id, user_id=alice.id,
         image_path="/x.png", method="forgeryscope", job_id=job["_id"])

    analysis = client.get(f"/analyses/{analysis_id}", headers=alice.headers).json()
    assert analysis["status"] == "completed"
    results = analysis["results"]
    assert results["method"] == "forgeryscope" and results["verdict"] == "duplicated"
    assert results["detections"] == [DETECTION] and results["panels"] == PANELS
    assert results["matches_image"] == "/m.png" and results["report"] == "/r.json"
    assert statuses == ["Matching 1 candidate panel pairs"]


def test_report_download(client, alice):
    report = UPLOAD_DIR / alice.id / "analyses" / "r" / "fig_result.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"verdict": "authentic"}')
    analysis_id = get_analyses_collection().insert_one({
        "type": "single_image_copy_move", "user_id": alice.id, "source_image_id": str(ObjectId()),
        "status": "completed", "created_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc),
        "results": {"method": "forgeryscope", "verdict": "authentic", "report": str(report)},
    }).inserted_id

    response = client.get(f"/analyses/{analysis_id}/results/report/download", headers=alice.headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"verdict": "authentic"}
    missing = client.get(f"/analyses/{analysis_id}/results/matches/download", headers=alice.headers)
    assert missing.status_code == 404
