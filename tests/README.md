# Tests

See [docs/development/testing.md](../docs/development/testing.md).

Quick start:

```bash
pip install -r requirements-dev.txt
pytest -m "not integration and not e2e"     # no services needed
```

- `tests/unit/`: fast tests on an in-memory database, no services.
- `tests/test_*.py` marked `integration`: need a MongoDB (`MONGODB_URL`).
- `tests/*_e2e.py`: need the full stack with the analysis tool images.

Tests always run against a throw-away `elies_test_*` database and a
temporary workspace (see `tests/conftest.py`), never against real data.
