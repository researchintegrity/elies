"""Task-status ownership (#55) and query safety (#57)."""
import pytest

import app.routes.documents as documents_routes
from app.db.mongodb import get_documents_collection, get_images_collection
from tests.unit.conftest import PDF_BYTES, PNG_BYTES


class FakeAsyncResult:
    def __init__(self, task_id, app=None):
        self.id = task_id
        self.status = self.state = "SUCCESS"
        self.result = self.info = {"result_panel_ids": [], "extracted_panels_count": 0}

    def successful(self):
        return True

    def failed(self):
        return False


@pytest.fixture
def fake_results(monkeypatch):
    import celery.result

    monkeypatch.setattr(documents_routes, "AsyncResult", FakeAsyncResult)
    monkeypatch.setattr(celery.result, "AsyncResult", FakeAsyncResult)


def test_document_task_status_is_owner_only(client, alice, bob, fake_results):
    upload = client.post("/documents/upload", headers=alice.headers,
                         files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")})
    assert upload.status_code == 201, upload.text
    task_id = get_documents_collection().find_one()["task_id"]

    assert client.get(f"/documents/tasks/{task_id}", headers=alice.headers).status_code == 200
    assert client.get(f"/documents/tasks/{task_id}", headers=bob.headers).status_code == 404
    assert client.get("/documents/tasks/made-up-id", headers=alice.headers).status_code == 404


def test_panel_task_status_is_owner_only(client, alice, bob, fake_results):
    image_id = client.post("/images/upload", headers=alice.headers,
                           files={"file": ("fig.png", PNG_BYTES, "image/png")}).json()["_id"]
    started = client.post("/images/extract-panels", headers=alice.headers, json={"image_ids": [image_id]})
    assert started.status_code == 202, started.text
    task_id = started.json()["task_id"]

    assert client.get(f"/images/extract-panels/status/{task_id}", headers=alice.headers).status_code == 200
    assert client.get(f"/images/extract-panels/status/{task_id}", headers=bob.headers).status_code == 404


def test_panel_extraction_request_size_is_bounded(client, alice):
    response = client.post("/images/extract-panels", headers=alice.headers, json={"image_ids": ["a"] * 21})
    assert response.status_code == 422


@pytest.mark.parametrize("term", ["(", "a+b*", ".*", "[x"])
def test_search_terms_are_literal(client, alice, term):
    get_images_collection().insert_one({"user_id": alice.id, "filename": "a+b*.png", "original_filename": "x"})

    ids = client.get("/images/ids", params={"search": term}, headers=alice.headers)
    api = client.get("/api/search", params={"query": term}, headers=alice.headers)
    docs = client.get("/api/documents", params={"search": term}, headers=alice.headers)

    assert ids.status_code == api.status_code == docs.status_code == 200
    if term == "a+b*":
        assert ids.json()["count"] == 1
    if term == ".*":
        assert ids.json()["count"] == 0


def test_admin_search_is_literal(client, alice):
    from bson import ObjectId

    from app.db.mongodb import get_users_collection

    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"roles": ["user", "admin"]}})
    token = client.post("/auth/login", data={"username": "alice", "password": alice.password}).json()["access_token"]
    response = client.get("/admin/users", params={"search": "(unclosed"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.json()["total"] == 0


@pytest.mark.parametrize(
    "path",
    ["/annotations/single?image_id=x&limit=100000", "/annotations/dual?source_image_id=x&limit=100000",
     "/api/documents?sort_by=hashed_password", "/api/search?query=a&page=100000"],
)
def test_unbounded_parameters_are_rejected(client, alice, path):
    assert client.get(path, headers=alice.headers).status_code == 422
