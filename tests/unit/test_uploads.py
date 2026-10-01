"""Upload safety: client filenames never choose storage paths (#50), batch job ids (#60)."""
from pathlib import Path

import pytest
from celery.app.task import Task

from app.config.settings import UPLOAD_DIR
from app.db.mongodb import get_documents_collection, get_images_collection, get_indexing_jobs_collection
from tests.unit.conftest import PDF_BYTES, PNG_BYTES

TRAVERSAL_NAMES = [
    "../../../{victim}/images/uploaded/evil.png",
    "..\\..\\..\\{victim}\\images\\uploaded\\evil.png",
    "/etc/{victim}/evil.png",
]


def _files_under(path: Path):
    return sorted(p for p in path.rglob("*") if p.is_file()) if path.exists() else []


@pytest.mark.parametrize("name_template", TRAVERSAL_NAMES)
def test_single_upload_ignores_path_components(client, alice, bob, name_template):
    bob_files_before = _files_under(UPLOAD_DIR / bob.id)
    name = name_template.format(victim=bob.id)

    response = client.post("/images/upload", headers=alice.headers, files={"file": (name, PNG_BYTES, "image/png")})

    assert response.status_code == 201, response.text
    body = response.json()
    stored = Path(body["file_path"])
    assert stored.parent == UPLOAD_DIR / alice.id / "images" / "uploaded"
    assert stored.name == f"{body['_id']}.png"
    assert stored.exists()
    assert body["original_filename"] == "evil.png"
    assert _files_under(UPLOAD_DIR / bob.id) == bob_files_before


def test_batch_upload_ignores_path_components(client, alice, bob):
    name = f"../../../{bob.id}/images/uploaded/evil.png"
    response = client.post(
        "/images/upload/batch",
        headers=alice.headers,
        files=[("files", (name, PNG_BYTES, "image/png"))],
    )
    assert response.status_code == 202, response.text
    (image_id,) = response.json()["image_ids"]
    stored = Path(get_images_collection().find_one()["file_path"])
    assert stored == UPLOAD_DIR / alice.id / "images" / "uploaded" / f"{image_id}.png"
    assert _files_under(UPLOAD_DIR / bob.id) == []


@pytest.mark.parametrize(
    "filename, content, detail",
    [
        ("fake.png", b"not an image at all", "not a valid image"),
        ("empty.png", b"", "empty"),
        ("script.html", PNG_BYTES, "Invalid file type"),
    ],
)
def test_invalid_images_are_rejected_without_leftovers(client, alice, filename, content, detail):
    response = client.post("/images/upload", headers=alice.headers, files={"file": (filename, content, "image/png")})
    assert response.status_code == 400
    assert detail in response.json()["detail"]
    assert get_images_collection().count_documents({}) == 0
    assert _files_under(UPLOAD_DIR / alice.id / "images") == []


def test_document_link_is_checked_before_saving(client, alice, bob):
    bob_doc = get_documents_collection().insert_one({"user_id": bob.id, "filename": "b.pdf"}).inserted_id

    bad_id = client.post("/images/upload?document_id=not-an-id", headers=alice.headers,
                         files={"file": ("a.png", PNG_BYTES, "image/png")})
    foreign = client.post(f"/images/upload?document_id={bob_doc}", headers=alice.headers,
                          files={"file": ("a.png", PNG_BYTES, "image/png")})

    assert bad_id.status_code == 400
    assert foreign.status_code == 404
    assert _files_under(UPLOAD_DIR / alice.id / "images") == []


def test_pdf_upload_stores_by_id_and_validates_content(client, alice, celery_calls):
    bad = client.post("/documents/upload", headers=alice.headers,
                      files={"file": ("../x.pdf", b"MZ not a pdf", "application/pdf")})
    assert bad.status_code == 400
    assert _files_under(UPLOAD_DIR / alice.id / "pdfs") == []

    good = client.post("/documents/upload", headers=alice.headers,
                       files={"file": ("../../paper.pdf", PDF_BYTES, "application/pdf")})
    assert good.status_code == 201, good.text
    body = good.json()
    assert body["filename"] == "paper.pdf"
    assert Path(body["file_path"]) == UPLOAD_DIR / alice.id / "pdfs" / f"{body['_id']}.pdf"
    assert [c.name for c in celery_calls] == ["tasks.extract_images"]


def test_concurrent_batches_get_distinct_job_ids(client, alice):
    first = client.post("/images/upload/batch", headers=alice.headers, files=[("files", ("a.png", PNG_BYTES, "image/png"))])
    second = client.post("/images/upload/batch", headers=alice.headers, files=[("files", ("b.png", PNG_BYTES, "image/png"))])
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] != second.json()["job_id"]
    assert get_indexing_jobs_collection().count_documents({}) == 2


def test_batch_rollback_removes_new_images_when_queueing_fails(client, alice, monkeypatch):
    def broken_apply_async(self, *args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(Task, "apply_async", broken_apply_async)
    response = client.post("/images/upload/batch", headers=alice.headers,
                           files=[("files", ("a.png", PNG_BYTES, "image/png"))])

    assert response.status_code == 500
    assert get_images_collection().count_documents({}) == 0
    assert _files_under(UPLOAD_DIR / alice.id / "images") == []
