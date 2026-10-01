"""Configuration is read from the environment and validated (#66); CORS is restricted (#53)."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _settings_in_subprocess(extra_env, expression):
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOST_WORKSPACE_PATH": "/tmp/host-ws",
        "CONTAINER_WORKSPACE_PATH": "/tmp/container-ws",
        "JWT_SECRET": "x" * 40,
        **extra_env,
    }
    code = (
        "import json\n"
        "from app.config import settings, storage_quota\n"
        f"print(json.dumps({expression}))\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    return result


def test_documented_variables_are_honoured():
    result = _settings_in_subprocess(
        {
            "ALLOWED_ORIGINS": "https://elies.example.org, https://other.example.org",
            "LOG_LEVEL": "debug",
            "PDF_MAX_SIZE_MB": "10",
            "IMAGE_MAX_SIZE_MB": "2",
            "DEFAULT_USER_STORAGE_QUOTA_GB": "0.5",
            "REDIS_HOST": "redis",
            "REDIS_PASSWORD": "s3cret",
        },
        "[settings.ALLOWED_ORIGINS, settings.LOG_LEVEL, storage_quota.MAX_PDF_FILE_SIZE,"
        " storage_quota.MAX_IMAGE_FILE_SIZE, storage_quota.DEFAULT_USER_STORAGE_QUOTA,"
        " settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND, settings.TRUFOR_USE_GPU]",
    )
    assert result.returncode == 0, result.stderr
    origins, level, pdf, img, quota, broker, backend, gpu = json.loads(result.stdout)
    assert origins == ["https://elies.example.org", "https://other.example.org"]
    assert level == "DEBUG"
    assert (pdf, img, quota) == (10 * 1024**2, 2 * 1024**2, 512 * 1024**2)
    assert broker == "redis://:s3cret@redis:6379/0"
    assert backend == "redis://:s3cret@redis:6379/1"
    assert gpu is False


def test_explicit_celery_urls_win():
    result = _settings_in_subprocess(
        {"CELERY_BROKER_URL": "redis://broker:1/0", "CELERY_RESULT_BACKEND": "redis://backend:2/1"},
        "[settings.CELERY_BROKER_URL, settings.CELERY_RESULT_BACKEND]",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["redis://broker:1/0", "redis://backend:2/1"]


@pytest.mark.parametrize(
    "env, message",
    [
        ({"HOST_WORKSPACE_PATH": ""}, "HOST_WORKSPACE_PATH environment variable must be set"),
        ({"CBIR_TIMEOUT": "soon"}, "CBIR_TIMEOUT must be an integer"),
    ],
)
def test_invalid_configuration_fails_with_a_clear_message(env, message):
    result = _settings_in_subprocess(env, "1")
    assert result.returncode != 0
    assert message in result.stderr


def test_celery_has_no_unknown_settings():
    from celery.app.defaults import NAMESPACES, flatten

    from app.celery_config import celery_app

    known = {key for key, _ in flatten(NAMESPACES)}
    custom = {key for key in celery_app.conf.changes if key != "deprecated_settings"}
    assert custom <= known, custom - known


def test_cors_only_allows_configured_origins(client):
    allowed = client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    denied = client.options(
        "/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers
