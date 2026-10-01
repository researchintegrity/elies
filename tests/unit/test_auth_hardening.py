"""Authentication hardening (#56)."""
import pytest
from bson import ObjectId

from app import cli
from app.config.settings import LOGIN_MAX_FAILURES, REGISTRATION_MAX_PER_HOUR
from app.db.mongodb import get_users_collection
from app.utils.security import verify_password_or_dummy


def _register(client, username="carol", email="carol@example.org", password="correct-horse-battery"):
    return client.post("/auth/register", json={"username": username, "email": email, "password": password})


def _login(client, username, password):
    return client.post("/auth/login", data={"username": username, "password": password})


@pytest.mark.parametrize(
    "payload_update, fragment",
    [
        ({"password": "short"}, "at least 12"),
        ({"password": "é" * 40}, "at most 72 bytes"),
        ({"username": "has space"}, "pattern"),
        ({"username": "$where"}, "pattern"),
    ],
)
def test_registration_validation(client, payload_update, fragment):
    payload = {"username": "carol", "email": "carol@example.org", "password": "correct-horse-battery", **payload_update}
    response = client.post("/auth/register", json=payload)
    assert response.status_code == 422
    assert fragment in response.text


def test_emails_are_case_insensitive(client):
    assert _register(client, email="Carol@Example.ORG").status_code == 200
    assert get_users_collection().find_one({"username": "carol"})["email"] == "carol@example.org"
    assert _register(client, username="carol2", email="CAROL@example.org").status_code == 400
    assert _login(client, "carol@EXAMPLE.org", "correct-horse-battery").status_code == 200


def test_repeated_login_failures_are_throttled(client, alice):
    for _ in range(LOGIN_MAX_FAILURES):
        assert _login(client, "alice", "wrong-password-123").status_code == 401

    blocked = _login(client, "alice", alice.password)
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_successful_login_resets_failure_count(client, alice):
    for _ in range(LOGIN_MAX_FAILURES - 1):
        _login(client, "alice", "wrong-password-123")
    assert _login(client, "alice", alice.password).status_code == 200
    assert _login(client, "alice", "wrong-password-123").status_code == 401


def test_registrations_per_address_are_limited(client):
    for i in range(REGISTRATION_MAX_PER_HOUR):
        assert _register(client, username=f"user{i}", email=f"user{i}@example.org").status_code == 200
    assert _register(client, username="one-more", email="one-more@example.org").status_code == 429


def test_unknown_user_still_spends_hash_time():
    assert verify_password_or_dummy("anything", None) is False


def test_profiles_by_username_are_admin_only(client, alice, bob):
    assert client.get("/users/bob", headers=alice.headers).status_code == 403
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"roles": ["user", "admin"]}})
    admin_token = _login(client, "alice", alice.password).json()["access_token"]
    response = client.get("/users/bob", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == 200 and response.json()["username"] == "bob"


def test_password_change_revokes_old_tokens(client, alice):
    wrong = client.put("/users/me/password", headers=alice.headers,
                       json={"current_password": "nope-nope-nope", "new_password": "a-brand-new-passphrase"})
    assert wrong.status_code == 400

    response = client.put("/users/me/password", headers=alice.headers,
                          json={"current_password": alice.password, "new_password": "a-brand-new-passphrase"})
    assert response.status_code == 200
    new_headers = {"Authorization": f"Bearer {response.json()['access_token']}"}

    assert client.get("/users/me", headers=alice.headers).status_code == 401
    assert client.get("/users/me", headers=new_headers).status_code == 200
    assert _login(client, "alice", "a-brand-new-passphrase").status_code == 200


def test_admin_reset_requires_password_change(client, alice, bob):
    get_users_collection().update_one({"_id": ObjectId(alice.id)}, {"$set": {"roles": ["user", "admin"]}})
    admin_headers = {"Authorization": f"Bearer {_login(client, 'alice', alice.password).json()['access_token']}"}

    reset = client.post(f"/admin/users/{bob.id}/reset-password", json={}, headers=admin_headers)
    temporary = reset.json()["generated_password"]

    login = _login(client, "bob", temporary)
    assert login.json()["user"]["must_change_password"] is True
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    changed = client.put("/users/me/password", headers=headers,
                         json={"current_password": temporary, "new_password": "bobs-own-passphrase"})
    assert changed.json()["user"]["must_change_password"] is False


def test_cli_creates_and_promotes_admins(mock_db, alice, capsys):
    assert cli.main(["create-admin", "--username", "root", "--email", "Root@Example.org", "--generate-password"]) == 0
    root = get_users_collection().find_one({"username": "root"})
    assert "admin" in root["roles"] and root["must_change_password"] is True and root["email"] == "root@example.org"
    assert "Generated password" in capsys.readouterr().out

    assert cli.main(["create-admin", "--username", "root", "--email", "x@example.org", "--password", "long-enough-pass"]) == 1
    assert cli.main(["promote", "--username", "alice"]) == 0
    assert "admin" in get_users_collection().find_one({"username": "alice"})["roles"]
    assert cli.main(["promote", "--username", "nobody"]) == 1
