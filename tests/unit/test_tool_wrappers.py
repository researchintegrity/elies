"""Each analysis tool runs through the shared runner with read-only inputs (#67)."""
import os
from pathlib import Path


import app.utils.docker_copy_move as copy_move
import app.utils.docker_extraction as extraction
import app.utils.docker_panel_extractor as panels
import app.utils.docker_trufor as trufor
import app.utils.docker_watermark as watermark
from app.config.settings import UPLOAD_DIR
from app.utils.docker_runner import ContainerRun
from tests.unit.conftest import PDF_BYTES, PNG_BYTES


class FakeRunner:
    """Stands in for run_tool_container; ``produce`` writes the tool's outputs."""

    def __init__(self, produce=None, returncode=0, stdout_lines=()):
        self.calls = []
        self.produce = produce
        self.returncode = returncode
        self.stdout_lines = stdout_lines

    def __call__(self, image, args=(), mounts=(), *, timeout, env=None, gpu=False, purpose="tool", on_stdout_line=None):
        self.calls.append({"image": image, "args": list(args), "mounts": list(mounts), "env": env or {}, "timeout": timeout})
        writable = {m.target: Path(m.source) for m in mounts if not m.read_only}
        if self.produce:
            self.produce(writable, list(args))
        for line in self.stdout_lines:
            on_stdout_line(line)
        return ContainerRun("elies-fake", self.returncode, "", "boom" if self.returncode else "", False, 0.1)

    def mounts(self):
        return {m.target: m for m in self.calls[-1]["mounts"]}


def _file(path: Path, content=PNG_BYTES) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_pdf_extraction_mounts_and_outputs(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d1.pdf", PDF_BYTES)
    fake = FakeRunner(produce=lambda out, args: [
        (out["/OUTPUT"] / "p-1-1.png").write_bytes(PNG_BYTES), (out["/OUTPUT"] / "notes.txt").write_text("x")])
    monkeypatch.setattr(extraction, "run_tool_container", fake)

    count, errors, files = extraction.extract_images_with_docker("d1", "u1", pdf)

    assert (count, errors) == (1, [])
    assert files[0]["filename"] == "p-1-1.png"
    mounts = fake.mounts()
    assert mounts["/INPUT"].read_only and not mounts["/OUTPUT"].read_only
    assert fake.calls[0]["env"] == {"INPUT_PATH": "/INPUT/d1.pdf", "OUTPUT_PATH": "/OUTPUT"}


def test_pdf_without_images_is_not_an_error(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d2.pdf", PDF_BYTES)
    monkeypatch.setattr(extraction, "run_tool_container", FakeRunner())
    assert extraction.extract_images_with_docker("d2", "u1", pdf) == (0, [], [])


def test_pdf_extraction_failure_is_reported(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d3.pdf", PDF_BYTES)
    monkeypatch.setattr(extraction, "run_tool_container", FakeRunner(returncode=1))
    count, errors, files = extraction.extract_images_with_docker("d3", "u1", pdf)
    assert count == 0 and "exit code 1" in errors[0]


def test_cross_copy_move_keypoint(monkeypatch):
    src = _file(UPLOAD_DIR / "u1" / "images" / "uploaded" / "a.png")
    tgt = _file(UPLOAD_DIR / "u1" / "images" / "extracted" / "d1" / "b.png")
    fake = FakeRunner(produce=lambda out, args: (out["/output_vol"] / "a_vs_b_matches.png").write_bytes(PNG_BYTES))
    monkeypatch.setattr(copy_move, "run_tool_container", fake)

    ok, message, results = copy_move.run_copy_move_detection_with_docker(
        "an1", "cross_image_copy_move", "u1", src, tgt, method="keypoint", descriptor="cv_sift")

    assert ok and results["matches_image"].endswith("a_vs_b_matches.png")
    args = fake.calls[0]["args"]
    assert args[:3] == ["--input", "/input_vol/a.png", "/target_vol/b.png"]
    assert "--descriptor" in args and "cv_sift" in args
    mounts = fake.mounts()
    assert mounts["/input_vol"].read_only and mounts["/target_vol"].read_only
    assert not mounts["/output_vol"].read_only


def test_trufor_forwards_status_lines(monkeypatch):
    src = _file(UPLOAD_DIR / "u1" / "images" / "uploaded" / "c.png")

    def produce(out, args):
        for key in ("pred_map", "conf_map"):
            (out["/data_out"] / f"c_{key}.png").write_bytes(PNG_BYTES)

    fake = FakeRunner(produce=produce, stdout_lines=["loading", "[STATUS] Running inference", "[STATUS] Done"])
    monkeypatch.setattr(trufor, "run_tool_container", fake)
    statuses = []

    ok, message, results = trufor.run_trufor_detection_with_docker("an2", "u1", src, status_callback=statuses.append)

    assert ok and set(results) == {"pred_map", "conf_map"}
    assert statuses == ["Running inference", "Done"]
    assert fake.mounts()["/data"].read_only
    assert "-1" in fake.calls[0]["args"]  # CPU unless TRUFOR_USE_GPU


def test_panel_extraction_input_is_read_only(monkeypatch):
    img = _file(UPLOAD_DIR / "u1" / "images" / "uploaded" / "fig.png")

    def produce(out, args):
        output = next(path for target, path in out.items() if target.endswith("/output"))
        (output / "PANELS.csv").write_text("FIGNAME,ID,LABEL,X0,Y0,X1,Y1\nfig,1,Blots,0,0,10,10\n")

    fake = FakeRunner(produce=produce)
    monkeypatch.setattr(panels, "run_tool_container", fake)

    ok, message, info = panels.extract_panels_with_docker(["img1"], "u1", [img])

    assert ok, message
    input_mount = next(m for m in fake.calls[0]["mounts"] if m.target.endswith("/input"))
    output_mount = next(m for m in fake.calls[0]["mounts"] if m.target.endswith("/output"))
    assert input_mount.read_only and not output_mount.read_only
    assert any(arg.endswith("/input/uploaded/fig.png") for arg in fake.calls[0]["args"])


def test_watermark_output_is_staged_then_moved(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d9.pdf", PDF_BYTES)
    fake = FakeRunner(produce=lambda out, args: (out["/output"] / args[args.index("-o") + 1].split("/")[-1]).write_bytes(PDF_BYTES))
    monkeypatch.setattr(watermark, "run_tool_container", fake)

    ok, message, info = watermark.remove_watermark_with_docker("d9", "u1", pdf, aggressiveness_mode=3)

    assert ok, message
    assert info["filename"] == "d9_watermark_removed_m3.pdf"
    assert Path(info["path"]).exists() and Path(info["path"]).parent == Path(pdf).parent
    assert fake.mounts()["/input"].read_only
    assert not os.listdir(Path(pdf).parent / ".watermark-staging")


def test_watermark_output_is_stored_under_the_new_document_id(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d11.pdf", PDF_BYTES)
    fake = FakeRunner(produce=lambda out, args: (out["/output"] / args[args.index("-o") + 1].split("/")[-1]).write_bytes(PDF_BYTES))
    monkeypatch.setattr(watermark, "run_tool_container", fake)

    ok, message, info = watermark.remove_watermark_with_docker("d11", "u1", pdf, aggressiveness_mode=2, output_id="abc123")

    assert ok, message
    assert Path(info["path"]).name == "abc123.pdf" and Path(info["path"]).exists()
    assert info["filename"] == "d11_watermark_removed_m2.pdf"


def test_watermark_failure_leaves_no_staging(monkeypatch):
    pdf = _file(UPLOAD_DIR / "u1" / "pdfs" / "d10.pdf", PDF_BYTES)
    monkeypatch.setattr(watermark, "run_tool_container", FakeRunner(returncode=3))
    ok, message, info = watermark.remove_watermark_with_docker("d10", "u1", pdf)
    assert not ok and "exit code 3" in message
    assert not os.listdir(Path(pdf).parent / ".watermark-staging")
