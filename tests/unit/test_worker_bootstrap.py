"""Celery workers register every task without importing the web app (#73)."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXPECTED_TASKS = {
    "tasks.cbir_delete_image",
    "tasks.cbir_delete_user_data",
    "tasks.cbir_index_batch",
    "tasks.cbir_index_batch_with_progress",
    "tasks.cbir_index_image",
    "tasks.cbir_search",
    "tasks.cbir_update_labels",
    "tasks.detect_copy_move",
    "tasks.detect_copy_move_cross",
    "tasks.delete_user_account",
    "tasks.detect_trufor",
    "tasks.extract_images",
    "tasks.extract_panels",
    "tasks.provenance_analysis",
    "tasks.reap_stale_jobs",
    "tasks.remove_watermark",
}


def test_worker_registers_all_tasks_without_loading_fastapi_app():
    code = (
        "import json, sys\n"
        "from app.celery_config import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "names = sorted(n for n in celery_app.tasks if n.startswith('tasks.'))\n"
        "print(json.dumps({'tasks': names, 'web_app_loaded': 'app.main' in sys.modules}))\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOST_WORKSPACE_PATH": "/tmp/elies-worker-ws",
        "CONTAINER_WORKSPACE_PATH": "/tmp/elies-worker-ws",
    }
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert EXPECTED_TASKS <= set(out["tasks"]), EXPECTED_TASKS - set(out["tasks"])
    assert out["web_app_loaded"] is False
