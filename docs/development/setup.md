# Development setup

## Prerequisites

- Docker with the Compose plugin
- Python 3.12 (to run the tests and tools outside Docker)
- Node.js (for the frontend in `system_modules/elies-frontend`)

## Run the stack

```bash
git clone --recurse-submodules https://github.com/researchintegrity/elies.git
cd elies
cp .env.example .env
```

In `.env`, set:

- `JWT_SECRET`: any random string of 32+ characters
  (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`);
- `HOST_WORKSPACE_PATH`: the absolute path of
  `system_modules/elies-frontend/workspace` in your clone (the API, the
  workers and the tool containers all mount it).

Then:

```bash
docker compose --profile tools build    # analysis tool images, slow, once
docker compose up -d                    # API with auto-reload, workers, beat, MongoDB, Redis, CBIR, provenance
docker compose exec api python -m app.cli create-admin --username admin --email admin@example.org --generate-password
```

The API is at http://localhost:8000 (interactive docs at `/docs`). Start the
frontend with `cd system_modules/elies-frontend && npm install && npm run dev`
and open http://localhost:5173.

Monitoring UIs (Flower for Celery, Attu for Milvus) start with
`docker compose --profile monitoring up -d`.

## Work on the code outside Docker

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install          # ruff on every commit
pytest -m "not integration and not e2e"
```

See [testing.md](testing.md) for the test layout and
[configuration.md](configuration.md) for every setting.
