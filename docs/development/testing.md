# Testing

Tests never touch real data: `tests/conftest.py` forces a throw-away database
named `elies_test_*` (it refuses any other name) and a temporary workspace
directory, and both are set before the application is imported.

## Test groups

| Group | Location / marker | Needs | Command |
|---|---|---|---|
| Unit | `tests/unit/`, plus unmarked tests in `tests/` | nothing: MongoDB is `mongomock`, Redis is `fakeredis`, Celery submissions are recorded, CBIR and Docker tools are stubbed | `pytest -m "not integration and not e2e"` |
| Integration | `@pytest.mark.integration` | a MongoDB on `MONGODB_URL` (and Redis for some) | `pytest -m "integration and not e2e"` |
| End-to-end | `tests/*_e2e.py` (marked `e2e` automatically) | the whole stack running with the tool images | `pytest -m e2e` |

CI (`.github/workflows/ci.yml`) runs ruff and the unit tests with coverage on
every push and pull request, and the integration tests against MongoDB and
Redis service containers.

## Running

```bash
pip install -r requirements-dev.txt
ruff check .
pytest -m "not integration and not e2e" --cov=app --cov-report=term
```

Integration and e2e tests use `tests/test.env` for defaults (local MongoDB,
Redis and services); real environment variables take precedence. Set
`TEST_DATABASE_NAME` (it must start with `elies_test`) to choose the
database name.

## Writing unit tests

Use the fixtures in `tests/unit/conftest.py`:

- `client`: a `TestClient` on an in-memory database;
- `alice`, `bob` (or `register("name")`): registered users with `.headers`
  for authenticated requests;
- `celery_calls`: the tasks queued during the test (`name`, `kwargs`);
- `mock_db`: the mongomock database, for direct inspection;
- `PNG_BYTES`, `PDF_BYTES`: small valid files to upload.

Celery tasks can be run in-process with `task.run(...)` after
`task.push_request(...)`; see `tests/unit/test_task_lifecycle.py`.

## API reference

`docs/api/endpoints.md` is generated from the OpenAPI schema. After changing
routes or their docstrings, run `python tools/generate_api_reference.py`;
`tests/unit/test_api_docs.py` fails while it is out of date.
