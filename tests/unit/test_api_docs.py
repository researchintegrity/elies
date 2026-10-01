"""The generated endpoint list (docs/api/endpoints.md) matches the code (#78)."""
import importlib.util
from pathlib import Path

from app.main import app

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "generate_api_reference.py"


def _generator():
    spec = importlib.util.spec_from_file_location("generate_api_reference", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_endpoint_reference_is_up_to_date():
    generator = _generator()
    assert generator.OUTPUT.read_text() == generator.render(app.openapi()), (
        "docs/api/endpoints.md is out of date: run python tools/generate_api_reference.py"
    )
