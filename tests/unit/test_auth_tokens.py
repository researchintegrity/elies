"""Token security (#51) and disabled accounts (#52)."""
import logging
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from bson import ObjectId

from app.db.mongodb import get_users_collection
from app.utils import security
from tests.unit.conftest import PNG_BYTES


@pytest.mark.parametrize(
    "value, message",
    [
        ("", "not set"),
        ("your-secret-key-here-change-this-in-production-to-a-strong-random-string", "placeholder"),
        ("short-secret", "at least 32"),
    ],
)
def test_insecure_jwt_secrets_are_refused(monkeypatch, value, message):
    monkeypatch.setenv("JWT_SECRET", value)
    with pytest.raises(RuntimeError, match=message):
        security._load_jwt_secret()


def _make_admin(user):
    get_users_collection().update_one({"_id": ObjectId(user.id)}, {"$set": {"roles": ["user", "admin"]}})


def test_token_signed_with_another_key_is_rejected(client, alice):
    forged = jwt.encode(
        {"uid": alice.id, "sub": "alice", "tv": 0, "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "x" * 40,
        algorithm="HS256",
    )
    assert client.get("/users/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_legacy_username_only_token_is_rejected(client, alice):
    legacy = jwt.encode(
        {"sub": "alice", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        security.JWT_SECRET,
        algorithm="HS256",
    )
    assert client.get("/users/me", headers={"Authorization": f"Bearer {legacy}"}).status_code == 401


@pytest.mark.parametrize("path", ["/users/me", "/documents", "/images", "/analyses", "/jobs"])
def test_disabled_account_is_blocked_everywhere(client, alice, path):
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"is_active": False}})
    assert client.get(path, headers=alice.headers).status_code == 403


def test_admin_deactivation_revokes_tokens_even_after_reactivation(client, alice, bob):
    _make_admin(alice)
    admin_headers = {"Authorization": f"Bearer {client.post('/auth/login', data={'username': 'alice', 'password': alice.password}).json()['access_token']}"}

    assert client.patch(f"/admin/users/{bob.id}/status", json={"is_active": False}, headers=admin_headers).status_code == 200
    assert client.patch(f"/admin/users/{bob.id}/status", json={"is_active": True}, headers=admin_headers).status_code == 200

    assert client.get("/documents", headers=bob.headers).status_code == 401
    fresh = client.post("/auth/login", data={"username": "bob", "password": bob.password})
    assert fresh.status_code == 200


def test_admin_password_reset_revokes_tokens(client, alice, bob):
    _make_admin(alice)
    admin_headers = {"Authorization": f"Bearer {client.post('/auth/login', data={'username': 'alice', 'password': alice.password}).json()['access_token']}"}

    response = client.post(f"/admin/users/{bob.id}/reset-password", json={}, headers=admin_headers)

    assert response.status_code == 200
    assert client.get("/users/me", headers=bob.headers).status_code == 401


def test_query_token_only_works_on_media_routes(client, alice):
    upload = client.post("/images/upload", headers=alice.headers, files={"file": ("a.png", PNG_BYTES, "image/png")})
    image_id = upload.json()["_id"]

    assert client.get(f"/images/{image_id}/thumbnail?token={alice.token}").status_code == 200
    assert client.get(f"/images/{image_id}/download?token={alice.token}").status_code == 200
    assert client.get(f"/images?token={alice.token}").status_code == 401
    assert client.delete(f"/images/{image_id}?token={alice.token}").status_code == 401


def test_access_log_filter_redacts_tokens():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/images/1/thumbnail?size=200&token=eyJabc.def.ghi", "1.1", 200),
        None,
    )
    security.RedactTokenFilter().filter(record)
    assert "eyJ" not in record.getMessage()
    assert "token=[REDACTED]" in record.getMessage()
