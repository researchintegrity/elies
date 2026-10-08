"""Error mapping (#62), field names (#63), deletion cascade (#65) and account deletion (#58)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bson import ObjectId

import app.routes.analyses as analyses_routes
from app.config.settings import UPLOAD_DIR
from app.db.mongodb import (
    get_analyses_collection,
    get_documents_collection,
    get_dual_annotations_collection,
    get_images_collection,
    get_jobs_collection,
    get_relationships_collection,
    get_single_annotations_collection,
    get_users_collection,
)
from app.tasks.maintenance import delete_user_account_task
from app.utils.file_storage import analysis_output_dir
from tests.unit.conftest import PDF_BYTES, PNG_BYTES


def _upload_image(client, user, name="a.png"):
    response = client.post("/images/upload", headers=user.headers, files={"file": (name, PNG_BYTES, "image/png")})
    assert response.status_code == 201, response.text
    return response.json()


def _make_admin(client, user):
    get_users_collection().update_one({"_id": ObjectId(user.id)}, {"$set": {"roles": ["user", "admin"]}})
    token = client.post("/auth/login", data={"username": user.username, "password": user.password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- #62 ----

@pytest.mark.parametrize(
    "method, path, expected",
    [
        ("get", "/analyses?type=cbir_search", 400),
        ("get", "/analyses/not-an-id", 400),
        ("get", f"/analyses/{ObjectId()}", 404),
        ("get", f"/analyses/{ObjectId()}/results/matches/download", 404),
        ("get", f"/api/documents/{ObjectId()}", 404),
        ("get", f"/images/{ObjectId()}/panels", 404),
        ("post", "/images/not-an-id/types", 400),
        ("get", f"/documents/{ObjectId()}/watermark-removal/status", 404),
        ("post", "/cbir/search", 422),
        ("post", "/provenance/analyze", 400),
    ],
)
def test_client_errors_are_not_500(client, alice, method, path, expected):
    body = {"types": ["figure"]} if path.endswith("/types") else {"image_id": "bad-id"}
    response = getattr(client, method)(path, headers=alice.headers, **({"json": body} if method == "post" else {}))
    assert response.status_code == expected, response.text


def test_api_delete_of_extracted_image_is_403(client, alice):
    image_id = get_images_collection().insert_one(
        {"user_id": alice.id, "filename": "e.png", "file_path": "/nope", "source_type": "extracted", "document_id": "d"}
    ).inserted_id
    assert client.delete(f"/api/images/{image_id}", headers=alice.headers).status_code == 403
    assert client.delete(f"/images/{image_id}", headers=alice.headers).status_code == 403


def test_unexpected_errors_do_not_leak_details(client, alice, monkeypatch):
    def boom():
        raise RuntimeError("mongodb://admin:secret@10.0.0.5 unreachable")

    monkeypatch.setattr(analyses_routes, "get_analyses_collection", boom)
    response = client.get("/analyses/stats", headers={**alice.headers, "Origin": "http://localhost:5173",
                                                      "X-Request-ID": "req-500"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    # The error response keeps the request ID and the CORS headers (the browser can read it)
    assert response.headers["X-Request-ID"] == "req-500"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------------------------------------------------------------- #63 ----

def test_select_all_ids_matches_gallery_filters(client, alice):
    first = _upload_image(client, alice)
    _upload_image(client, alice, "b.png")
    client.patch(f"/images/{first['_id']}/flag", headers=alice.headers)

    for params in ({"date_from": "2000-01-01"}, {"flagged": "true"}, {"search": "b.png"}, {}):
        gallery = client.get("/images", params=params, headers=alice.headers).json()["total"]
        ids = client.get("/images/ids", params=params, headers=alice.headers).json()["count"]
        assert ids == gallery, params


def test_dashboard_reports_stored_usage_and_limit(client, alice):
    get_users_collection().update_one(
        {"_id": ObjectId(alice.id)}, {"$set": {"storage_used_bytes": 12345, "storage_limit_bytes": 100000}}
    )
    data = client.get("/api/dashboard/stats", headers=alice.headers).json()["data"]
    assert (data["storage_used"], data["storage_limit"], data["storage_remaining"]) == (12345, 100000, 87655)


# ---------------------------------------------------------------- #65 ----

def test_image_with_missing_file_can_be_deleted(client, alice):
    image = _upload_image(client, alice)
    Path(image["file_path"]).unlink()
    assert client.delete(f"/images/{image['_id']}", headers=alice.headers).status_code == 204
    assert get_images_collection().count_documents({}) == 0


def _derived_artifacts(user_id, image_id):
    panel_dir = UPLOAD_DIR / user_id / "images" / "panels" / image_id
    panel_dir.mkdir(parents=True)
    panel_file = panel_dir / "p1.png"
    panel_file.write_bytes(PNG_BYTES)
    panel_id = get_images_collection().insert_one(
        {"user_id": user_id, "source_type": "panel", "source_image_id": image_id, "file_path": str(panel_file)}
    ).inserted_id
    thumb = UPLOAD_DIR / user_id / "images" / "thumbnails" / f"{image_id}.jpg"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    thumb.write_bytes(b"jpg")
    other = str(ObjectId())
    get_single_annotations_collection().insert_one({"user_id": user_id, "image_id": image_id})
    get_dual_annotations_collection().insert_one({"user_id": user_id, "source_image_id": other, "target_image_id": image_id})
    get_relationships_collection().insert_one({"user_id": user_id, "image1_id": image_id, "image2_id": other})
    analysis_id = get_analyses_collection().insert_one(
        {"user_id": user_id, "type": "trufor", "source_image_id": image_id, "created_at": datetime.now(timezone.utc)}
    ).inserted_id
    out = analysis_output_dir(user_id, str(analysis_id), "trufor")
    out.mkdir(parents=True)
    (out / "pred.png").write_bytes(b"x")
    return {"panel_id": panel_id, "panel_dir": panel_dir, "thumb": thumb, "analysis_dir": out}


def _assert_all_gone(artifacts):
    assert get_images_collection().count_documents({"_id": artifacts["panel_id"]}) == 0
    assert get_single_annotations_collection().count_documents({}) == 0
    assert get_dual_annotations_collection().count_documents({}) == 0
    assert get_relationships_collection().count_documents({}) == 0
    assert get_analyses_collection().count_documents({}) == 0
    assert not artifacts["panel_dir"].exists()
    assert not artifacts["thumb"].exists()
    assert not artifacts["analysis_dir"].exists()


def test_image_deletion_cascades(client, alice):
    image = _upload_image(client, alice)
    artifacts = _derived_artifacts(alice.id, image["_id"])

    assert client.delete(f"/images/{image['_id']}", headers=alice.headers).status_code == 204

    assert not Path(image["file_path"]).exists()
    _assert_all_gone(artifacts)


def test_document_deletion_cascades_and_keeps_linked_uploads(client, alice):
    doc = client.post("/documents/upload", headers=alice.headers, files={"file": ("p.pdf", PDF_BYTES, "application/pdf")}).json()
    extracted_dir = UPLOAD_DIR / alice.id / "images" / "extracted" / doc["_id"]
    extracted_file = extracted_dir / "x.png"
    extracted_file.write_bytes(PNG_BYTES)
    extracted_id = str(get_images_collection().insert_one(
        {"user_id": alice.id, "source_type": "extracted", "document_id": doc["_id"], "file_path": str(extracted_file)}
    ).inserted_id)
    artifacts = _derived_artifacts(alice.id, extracted_id)
    linked = client.post(f"/images/upload?document_id={doc['_id']}", headers=alice.headers,
                         files={"file": ("l.png", PNG_BYTES, "image/png")}).json()

    Path(doc["file_path"]).unlink()  # already missing on disk: must not block deletion
    assert client.delete(f"/documents/{doc['_id']}", headers=alice.headers).status_code == 204

    assert get_documents_collection().count_documents({}) == 0
    assert not extracted_dir.exists()
    _assert_all_gone(artifacts)
    kept = get_images_collection().find_one({"_id": ObjectId(linked["_id"])})
    assert kept is not None and kept["document_id"] is None


def test_analysis_deletion_removes_results_and_references(client, alice):
    image = _upload_image(client, alice)
    artifacts = _derived_artifacts(alice.id, image["_id"])
    analysis = get_analyses_collection().find_one()
    get_images_collection().update_one({"_id": ObjectId(image["_id"])}, {"$set": {"analysis_ids": [str(analysis["_id"])]}})

    assert client.delete(f"/analyses/{analysis['_id']}", headers=alice.headers).status_code == 200
    assert not artifacts["analysis_dir"].exists()
    assert get_images_collection().find_one({"_id": ObjectId(image["_id"])})["analysis_ids"] == []


# ---------------------------------------------------------------- #58 ----

def test_deleting_own_account_removes_everything(client, alice, bob, celery_calls):
    _upload_image(client, alice)
    client.post("/documents/upload", headers=alice.headers, files={"file": ("p.pdf", PDF_BYTES, "application/pdf")})
    bob_image = _upload_image(client, bob)

    response = client.delete("/users/me", headers=alice.headers)
    assert response.status_code == 200
    assert client.get("/users/me", headers=alice.headers).status_code == 401
    queued = [c for c in celery_calls if c.name == "tasks.delete_user_account"]
    assert queued and queued[0].kwargs == {"user_id": alice.id}

    delete_user_account_task.run(user_id=alice.id)

    assert get_users_collection().count_documents({"_id": ObjectId(alice.id)}) == 0
    for collection in (get_documents_collection(), get_images_collection(), get_jobs_collection()):
        assert collection.count_documents({"user_id": alice.id}) == 0
    assert not (UPLOAD_DIR / alice.id).exists()
    assert Path(bob_image["file_path"]).exists()
    assert get_images_collection().count_documents({"user_id": bob.id}) == 1


def test_account_deletion_runs_inline_when_queue_is_down(client, alice, monkeypatch):
    from celery.app.task import Task

    def broken(self, *args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(Task, "apply_async", broken)
    assert client.delete("/users/me", headers=alice.headers).status_code == 200
    assert get_users_collection().count_documents({"_id": ObjectId(alice.id)}) == 0


def test_admin_user_deletion_rules(client, alice, bob, register, celery_calls):
    admin = _make_admin(client, alice)
    carol = register("carol")
    _make_admin(client, carol)

    assert client.delete(f"/admin/users/{alice.id}", headers=admin).status_code == 400
    assert client.delete(f"/admin/users/{carol.id}", headers=admin).status_code == 403
    assert client.delete(f"/admin/users/{bob.id}", headers=bob.headers).status_code == 403
    assert client.delete(f"/admin/users/{bob.id}", headers=admin).status_code == 200
    assert get_users_collection().find_one({"_id": ObjectId(bob.id)})["is_active"] is False
    assert any(c.name == "tasks.delete_user_account" for c in celery_calls)
