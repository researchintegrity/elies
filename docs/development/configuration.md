# Configuration

ELIES is configured with environment variables. `docker compose` reads them
from `.env` (copy `.env.example`) and passes them to the API, worker and beat
containers. Only `JWT_SECRET`, `HOST_WORKSPACE_PATH` and
`CONTAINER_WORKSPACE_PATH` are required; everything else has a default.

## Required

| Variable | Description |
|---|---|
| `JWT_SECRET` | Key that signs login tokens, at least 32 characters. The API refuses to start without it. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Anyone who knows it can sign in as any user. |
| `HOST_WORKSPACE_PATH` | Absolute path of the workspace on the host (uploads, extracted images, results). Mounted into the API, the workers and the analysis tool containers. |
| `CONTAINER_WORKSPACE_PATH` | Where the workspace is mounted inside the API and worker containers (`.env.example`: `/container-workspace`). |

## Network and services

| Variable | Default | Description |
|---|---|---|
| `API_BIND_ADDRESS` | `0.0.0.0` | Host interface the API port is published on. |
| `API_PORT` | `8000` | API port. |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface for MongoDB, Redis, Milvus, MinIO, CBIR, provenance, Flower and Attu. Keep it local. |
| `ALLOWED_ORIGINS` | local frontend dev servers | Comma-separated browser origins allowed by CORS. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Addresses whose `X-Forwarded-For` header uvicorn trusts. Set `*` when the API is reachable only through a reverse proxy (`API_BIND_ADDRESS=127.0.0.1`): requests then reach the container from the Docker gateway, and without it every client shares one per-IP rate limit. |
| `MONGODB_URL` | `mongodb://localhost:27017` | Include credentials when MongoDB authentication is on. |
| `DATABASE_NAME` | `elies_system` | Use `elis_system` to keep data from installs made before the ELIES rename. |
| `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD` | empty | Create a MongoDB root user when the data volume is first initialised. |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB` | `localhost`, `6379`, `0` | Celery broker (DB n) and result backend (DB n+1). |
| `REDIS_PASSWORD` | empty | Require a password on Redis (recommended). |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | built from `REDIS_*` | Override the Celery URLs. |
| `JOB_EVENTS_REDIS_URL` | broker URL | Redis used to publish job events to `/jobs/stream`. |
| `CBIR_SERVICE_HOST`, `CBIR_SERVICE_PORT` | `localhost`, `8001` | CBIR microservice. |
| `PROVENANCE_SERVICE_HOST`, `PROVENANCE_SERVICE_PORT` | `localhost`, `8002` | Provenance microservice. |
| `WORKER_CONCURRENCY` | `1` | Tasks per worker container (`docker-compose-prod.yml`). |

## Accounts and authentication

| Variable | Default | Description |
|---|---|---|
| `JWT_EXPIRATION_HOURS` | `24` | Token lifetime. |
| `PASSWORD_MIN_LENGTH` | `12` | Minimum password length (passwords are limited to 72 bytes by bcrypt). |
| `BCRYPT_ROUNDS` | `12` | Password hashing cost. Never lower it in a deployment. |
| `LOGIN_MAX_FAILURES` | `10` | Failed logins allowed per account and IP within the window. |
| `LOGIN_FAILURE_WINDOW_SECONDS` | `900` | Window for the login limit. |
| `REGISTRATION_MAX_PER_HOUR` | `20` | Registrations allowed per IP per hour. |

The login and registration limits are shared by all API processes through Redis; while Redis is unreachable each process counts on its own.

## Storage and uploads

| Variable | Default | Description |
|---|---|---|
| `DEFAULT_USER_STORAGE_QUOTA_GB` | `1` | Quota of new users (admins can change it per user). |
| `PDF_MAX_SIZE_MB` | `500` | Largest PDF upload. |
| `IMAGE_MAX_SIZE_MB` | `100` | Largest image upload. |
| `MAX_IMAGE_PIXELS` | `200000000` | Largest image (width x height); protects against decompression bombs. |
| `MAX_BATCH_UPLOAD_FILES` | `200` | Files per batch upload. |
| `MAX_IDS_PER_REQUEST` | `1000` | IDs accepted in one request (indexing, search, deletion). |
| `MAX_SELECT_ALL_IDS` | `10000` | IDs returned by `GET /images/ids`. |
| `MAX_IMAGES_PER_EXTRACTION` | `20` | Images per panel extraction request. |

## Jobs and workers

| Variable | Default | Description |
|---|---|---|
| `MAX_ACTIVE_JOBS_PER_USER` | `20` | Analysis jobs a user may have waiting or running (`0` disables the limit). |
| `JOB_RETENTION_DAYS` | `7` | Days finished (or abandoned) jobs are kept. |
| `STALE_PROCESSING_MINUTES` | `60` | A running job not updated for this long is marked failed by the beat service. |
| `STALE_PENDING_HOURS` | `24` | A queued job older than this is marked failed. |
| `INDEXING_BATCH_CHUNK_SIZE` | `16` | Images sent to CBIR per indexing request. |
| `RELATIONSHIP_GRAPH_MAX_NODES` | `2000` | Images explored when building a relationship graph. |

## Analysis tools

The workers run each analysis tool in its own container.

| Variable | Default | Description |
|---|---|---|
| `DOCKER_BINARY` | `docker` | Docker CLI used by the workers. |
| `DOCKER_TOOL_NETWORK` | `none` | Network of tool containers. The tools need no network (model weights are in the images). |
| `DOCKER_TOOL_MEMORY`, `DOCKER_TOOL_CPUS` | unlimited | Resource limits per tool container (e.g. `4g`, `2`). |
| `DOCKER_TOOL_PIDS_LIMIT` | `1024` | Process limit per tool container. |
| `DOCKER_TOOL_USER` | image default | Run tools as this user (e.g. `1000:1000`); the output folders must be writable by it. |
| `TRUFOR_USE_GPU` | `false` | Run TruFor on an NVIDIA GPU (needs the NVIDIA container runtime). |
| `DOCKER_EXTRACTION_TIMEOUT` | `300` | Seconds for PDF image extraction. |
| `PANEL_EXTRACTION_TIMEOUT` | `600` | Seconds for panel extraction. |
| `COPY_MOVE_DETECTION_TIMEOUT`, `COPY_MOVE_KEYPOINT_TIMEOUT` | `600` | Seconds for copy-move detection (dense, keypoint). |
| `TRUFOR_TIMEOUT` | `600` | Seconds for TruFor. |
| `WATERMARK_REMOVAL_TIMEOUT` | `300` | Seconds for watermark removal. |
| `CBIR_TIMEOUT` | `120` | Seconds for a CBIR request. |
| `CBIR_HEALTH_CACHE_SECONDS` | `15` | How long a CBIR health check result is reused. |
| `PROVENANCE_TIMEOUT` | `600` | Seconds for a provenance request. |

## Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Level for the API and the workers. |
| `LOG_FORMAT` | `text` | `json` writes one JSON object per line (for log collectors). Every line carries the request ID. |
