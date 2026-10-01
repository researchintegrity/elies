"""Logging, health checks, request IDs and the admin audit log (#77)."""
import json
import logging

from bson import ObjectId

import app.main as main_module
from app.celery_config import bind_request_id, propagate_request_id, unbind_request_id
from app.db.mongodb import get_admin_audit_log_collection, get_jobs_collection, get_users_collection
from app.logging_config import JsonFormatter
from app.request_context import RequestIdFilter, request_id_var
from tests.unit.conftest import PNG_BYTES


def test_liveness_needs_no_dependencies(client, monkeypatch):
    monkeypatch.setattr(main_module, "_database_ready", lambda: False)
    assert client.get("/health/live").status_code == 200


def test_readiness_reports_503_without_internal_details(client, monkeypatch):
    assert client.get("/health/ready").status_code == 200
    assert client.get("/health").json()["status"] == "healthy"

    monkeypatch.setattr(main_module, "_redis_ready", lambda: False)
    for path in ("/health/ready", "/health"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["redis"] == "disconnected" and "error" not in response.json()


def test_request_id_is_echoed_or_generated(client):
    echoed = client.get("/health/live", headers={"X-Request-ID": "trace-123"})
    assert echoed.headers["X-Request-ID"] == "trace-123"
    generated = client.get("/health/live", headers={"X-Request-ID": "bad id with spaces"})
    assert generated.headers["X-Request-ID"] != "bad id with spaces" and len(generated.headers["X-Request-ID"]) == 32


def test_jobs_record_the_request_that_started_them(client, alice):
    image_id = client.post("/images/upload", headers=alice.headers,
                           files={"file": ("a.png", PNG_BYTES, "image/png")}).json()["_id"]
    response = client.delete(f"/images/{image_id}", headers={**alice.headers, "X-Request-ID": "req-delete-1"})
    assert response.status_code == 204
    assert get_jobs_collection().find_one({"job_type": "image_deletion"})["request_id"] == "req-delete-1"


def test_request_id_travels_with_celery_tasks():
    token = request_id_var.set("req-42")
    headers = {}
    propagate_request_id(headers=headers)
    request_id_var.reset(token)
    assert headers == {"request_id": "req-42"}

    class FakeTask:
        class request:
            request_id = "req-42"
            headers = None

    bind_request_id(task=FakeTask())
    assert request_id_var.get() == "req-42"
    unbind_request_id()
    assert request_id_var.get() is None


def test_json_log_lines_carry_the_request_id():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    token = request_id_var.set("req-7")
    RequestIdFilter().filter(record)
    request_id_var.reset(token)
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello world" and line["request_id"] == "req-7" and line["level"] == "INFO"


def test_admin_actions_are_audited(client, alice, bob):
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"roles": ["user", "admin"]}})
    token = client.post("/auth/login", data={"username": alice.username, "password": alice.password}).json()["access_token"]
    admin = {"Authorization": f"Bearer {token}", "X-Request-ID": "audit-1"}

    assert client.patch(f"/admin/users/{bob.id}/quota", headers=admin,
                        json={"storage_limit_bytes": 2 * 1024 ** 3}).status_code == 200
    assert client.patch(f"/admin/users/{bob.id}/status", headers=admin, json={"is_active": False}).status_code == 200

    entries = list(get_admin_audit_log_collection().find().sort("created_at", 1))
    assert [e["action"] for e in entries] == ["update_quota", "deactivated"]
    assert entries[0]["admin_username"] == "alice" and entries[0]["target_user_id"] == bob.id
    assert entries[0]["details"]["new"] == 2 * 1024 ** 3 and entries[0]["request_id"] == "audit-1"

    listing = client.get("/admin/audit-log", headers=admin, params={"target_user_id": bob.id}).json()
    assert listing["total"] == 2 and listing["items"][0]["action"] == "deactivated"
    assert client.get("/admin/audit-log", headers=bob.headers).status_code in (401, 403)


def test_readiness_pings_redis_even_during_the_back_off(client, monkeypatch):
    from app.utils import redis_client

    # A feature hit a Redis error a moment ago, but Redis answers now
    monkeypatch.setattr(redis_client, "_unavailable_until", float("inf"))
    assert client.get("/health/ready").status_code == 200
    assert redis_client.get_redis() is not None  # the successful ping ended the back-off
