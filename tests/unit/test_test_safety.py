"""Guards for issue #54: tests must never touch real data."""
import os
import tempfile

from app.config import settings
from app.db.mongodb import get_database_name


def test_database_name_is_throwaway():
    assert get_database_name().startswith("elies_test")
    assert get_database_name() != "elies_system"


def test_workspace_is_temporary():
    assert str(settings.UPLOAD_DIR).startswith(tempfile.gettempdir())
    assert "elies-test-workspace-" in os.environ["HOST_WORKSPACE_PATH"]
