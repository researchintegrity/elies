"""Dependency and code hygiene (#75)."""
import bcrypt

from app import __version__
from app.utils.security import hash_password, verify_password


def test_hashes_created_by_passlib_still_verify():
    # passlib's bcrypt handler produced standard $2b$ hashes of the first 72 bytes
    long_password = "p" * 80
    legacy_hash = bcrypt.hashpw(long_password.encode()[:72], bcrypt.gensalt(rounds=4)).decode()
    assert verify_password(long_password, legacy_hash)
    assert not verify_password("p" * 71, legacy_hash)


def test_password_hashing_round_trip():
    hashed = hash_password("correct-horse-battery")
    assert hashed.startswith("$2b$")
    assert verify_password("correct-horse-battery", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", "not-a-bcrypt-hash")
    assert not verify_password("anything", None)


def test_single_version_string(client):
    assert client.get("/").json()["version"] == __version__
    assert client.get("/openapi.json").json()["info"]["version"] == __version__


# ---------------------------------------------------------------- #74 ----

def _upload_pdf(client, user):
    from tests.unit.conftest import PDF_BYTES

    response = client.post("/documents/upload", headers=user.headers,
                           files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")})
    assert response.status_code == 201, response.text
    return response.json()


def test_datetimes_are_returned_as_utc_with_offset(client, alice):
    from datetime import datetime

    doc = _upload_pdf(client, alice)
    uploaded = client.get(f"/documents/{doc['_id']}", headers=alice.headers).json()["uploaded_date"]
    parsed = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def test_document_details_use_the_response_model(client, alice):
    doc = _upload_pdf(client, alice)
    body = client.get(f"/documents/{doc['_id']}", headers=alice.headers).json()
    # Fields the frontend reads while polling extraction
    assert body["filename"] == "paper.pdf"
    assert body["extraction_status"] == "pending" and body["extracted_image_count"] == 0
    # Internal fields are no longer leaked
    assert "task_id" not in body
    assert body["user_storage_used"] > 0


def test_legacy_api_routes_are_marked_deprecated(client, alice):
    response = client.get("/api/dashboard/stats", headers=alice.headers)
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"
    paths = client.get("/openapi.json").json()["paths"]
    assert paths["/api/dashboard/stats"]["get"]["deprecated"] is True
    assert paths["/images/types/all"]["get"]["deprecated"] is True
    assert "deprecated" not in paths["/images/tags"]["get"]


def test_type_updates_return_the_full_image(client, alice):
    from tests.unit.conftest import PNG_BYTES

    image = client.post("/images/upload", headers=alice.headers,
                        files={"file": ("a.png", PNG_BYTES, "image/png")}).json()
    client.patch(f"/images/{image['_id']}/flag", headers=alice.headers, json={"is_flagged": True})
    body = client.post(f"/images/{image['_id']}/types", headers=alice.headers, json={"types": ["figure"]}).json()
    assert body["image_type"] == ["figure"]
    assert body["user_storage_used"] == len(PNG_BYTES)
    assert body["is_flagged"] is True
