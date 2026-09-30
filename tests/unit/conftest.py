"""
Fixtures for fast unit tests that need no running services.

- MongoDB is replaced by an in-memory ``mongomock`` database per test.
- Celery ``.delay()`` / ``.apply_async()`` calls are recorded instead of being sent
  to Redis (see the ``celery_calls`` fixture).
- The CBIR health check and ExifTool are stubbed out.
"""
import sys
import uuid
from types import SimpleNamespace

import mongomock
import pytest
from celery.app.task import Task
from celery.result import AsyncResult
from fastapi.testclient import TestClient

import app.utils.docker_cbir as docker_cbir
import app.utils.metadata_parser as metadata_parser
from app.celery_config import celery_app
from app.db import mongodb
from app.main import app

# Smallest valid PNG (1x1 transparent pixel)
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)
# Minimal PDF header, enough for extension/size validation
PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _patch_everywhere(monkeypatch, original, replacement):
    """Replace a function in every loaded module that imported it by name."""
    for module in list(sys.modules.values()):
        if module is None or not getattr(module, "__name__", "").startswith("app"):
            continue
        for attr, value in list(vars(module).items()):
            if value is original:
                monkeypatch.setattr(module, attr, replacement)


@pytest.fixture
def mock_db(monkeypatch):
    client = mongomock.MongoClient()
    db = client["elies_test_unit"]
    monkeypatch.setattr(mongodb.db_connection, "_client", client)
    monkeypatch.setattr(mongodb.db_connection, "_db", db)
    monkeypatch.setattr(mongodb.db_connection, "connect", lambda: None)
    yield db


@pytest.fixture(autouse=True)
def celery_calls(monkeypatch):
    """Record Celery task submissions instead of talking to a broker."""
    calls = []

    def fake_apply_async(self, args=None, kwargs=None, **options):
        task_id = options.get("task_id") or uuid.uuid4().hex
        calls.append(SimpleNamespace(name=self.name, args=tuple(args or ()), kwargs=dict(kwargs or {}), id=task_id))
        return AsyncResult(task_id, app=celery_app)

    monkeypatch.setattr(Task, "apply_async", fake_apply_async)
    return calls


@pytest.fixture(autouse=True)
def stub_external_services(monkeypatch):
    """Make CBIR look healthy and skip ExifTool (not installed in CI)."""
    _patch_everywhere(monkeypatch, docker_cbir.check_cbir_health, lambda: (True, "ok"))
    _patch_everywhere(monkeypatch, metadata_parser.extract_exif_metadata, lambda path: {})


@pytest.fixture
def client(mock_db):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def register(client):
    """Register a user through the API and return (token, user_id, headers)."""

    def _register(username="alice", password="correct-horse-battery", email=None):
        response = client.post(
            "/auth/register",
            json={"username": username, "email": email or f"{username}@example.org", "password": password},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        token = body["access_token"]
        return SimpleNamespace(
            token=token,
            id=body["user"]["_id"],
            username=username,
            password=password,
            headers={"Authorization": f"Bearer {token}"},
        )

    return _register


@pytest.fixture
def alice(register):
    return register("alice")


@pytest.fixture
def bob(register):
    return register("bob")
