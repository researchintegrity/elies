"""
Shared pytest configuration.

Safety rules enforced here (see issue #54):
- Tests never touch the production database: DATABASE_NAME is forced to a
  throw-away name starting with ``elies_test``.
- Tests never write to a developer's real workspace: HOST_WORKSPACE_PATH and
  CONTAINER_WORKSPACE_PATH point at a fresh temporary directory.

Both must be set *before* any ``app`` module is imported, because the settings
module reads them at import time.
"""
import os
import shutil
import tempfile
import uuid

import pytest
from dotenv import load_dotenv

TEST_DB_PREFIX = "elies_test"

_current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_current_dir, "test.env"), override=False)

TEST_DATABASE_NAME = os.environ.get("TEST_DATABASE_NAME") or f"{TEST_DB_PREFIX}_{uuid.uuid4().hex[:8]}"
if not TEST_DATABASE_NAME.startswith(TEST_DB_PREFIX):
    raise pytest.UsageError(
        f"Refusing to run tests against database '{TEST_DATABASE_NAME}': "
        f"TEST_DATABASE_NAME must start with '{TEST_DB_PREFIX}'."
    )
os.environ["DATABASE_NAME"] = TEST_DATABASE_NAME

_TEST_WORKSPACE = tempfile.mkdtemp(prefix="elies-test-workspace-")
os.environ["HOST_WORKSPACE_PATH"] = _TEST_WORKSPACE
os.environ["CONTAINER_WORKSPACE_PATH"] = _TEST_WORKSPACE

from fastapi.testclient import TestClient  # noqa: E402

from app.db.mongodb import db_connection, get_users_collection  # noqa: E402
from app.main import app  # noqa: E402


def pytest_collection_modifyitems(config, items):
    """Mark tests that need the full running stack so they can be deselected."""
    for item in items:
        if item.path.name.endswith("_e2e.py"):
            item.add_marker(pytest.mark.e2e)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_WORKSPACE, ignore_errors=True)


@pytest.fixture(scope="session")
def mongodb_connection():
    """Connect to a real MongoDB using a throw-away test database."""
    db_connection._client = None
    db_connection._db = None
    db_connection.connect()

    yield db_connection

    try:
        assert TEST_DATABASE_NAME.startswith(TEST_DB_PREFIX)
        db_connection._client.drop_database(TEST_DATABASE_NAME)
        db_connection.disconnect()
    except Exception as e:
        print(f"Cleanup error: {e}")


@pytest.fixture(scope="function")
def client(mongodb_connection):
    """FastAPI TestClient for API testing"""
    return TestClient(app)


@pytest.fixture(scope="function")
def clean_users_collection(mongodb_connection):
    """Clean users collection before each test"""
    collection = get_users_collection()
    collection.drop()
    collection.create_index("username", unique=True)
    collection.create_index("email", unique=True)
    yield collection
    collection.drop()


@pytest.fixture
def test_user_data():
    """Test user data for registration"""
    return {
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "Test@Password123",
        "full_name": "Test User"
    }


@pytest.fixture
def test_user_data_2():
    """Second test user data for multi-user tests"""
    return {
        "username": "testuser2",
        "email": "testuser2@example.com",
        "password": "Test@Password456",
        "full_name": "Test User 2"
    }


@pytest.fixture(autouse=True)
def reset_rate_limits():
    """Login/registration limiters are process-global: start every test with a clean slate."""
    from app.routes import auth

    for limiter in (auth.login_failures_by_account, auth.login_failures_by_ip, auth.registrations_by_ip):
        limiter.clear()
