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
