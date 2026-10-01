# Deployment

This guide covers running ELIES on a server with Docker Compose. For a local
development setup see [setup.md](setup.md); every setting is listed in
[configuration.md](configuration.md).

## 1. Prepare the host

- Docker Engine with the Compose plugin. For TruFor on GPU, the NVIDIA
  container runtime.
- Enough disk for the workspace (uploads and results), MongoDB and Milvus.
- The repository with its submodules:

  ```bash
  git clone --recurse-submodules https://github.com/researchintegrity/elies.git
  cd elies
  ```

## 2. Configure

```bash
cp .env.example .env
```

Set at least:

| Setting | Value |
|---|---|
| `JWT_SECRET` | A random secret of 32+ characters: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Keep it private; changing it signs everyone out. |
| `HOST_WORKSPACE_PATH` | Absolute path of the workspace directory on the host. |
| `CONTAINER_WORKSPACE_PATH` | Keep `/container-workspace`. |
| `ALLOWED_ORIGINS` | The URL(s) the frontend is served from, e.g. `https://elies.example.org`. |
| `REDIS_PASSWORD` | A random password (the broker accepts tasks from anyone who can reach it). |
| `MONGO_ROOT_USERNAME`, `MONGO_ROOT_PASSWORD` | Optional MongoDB root user. Only applied when the MongoDB volume is created; add the credentials to `MONGODB_URL` (`mongodb://user:password@mongo:27017/?authSource=admin`). |

## 3. Network exposure

The compose files publish:

- the **API** on `API_BIND_ADDRESS:API_PORT` (default `0.0.0.0:8000`);
- **everything else** (MongoDB, Redis, Milvus, MinIO, CBIR, provenance,
  Flower, Attu) on `BIND_ADDRESS`, `127.0.0.1` by default, so these services
  are reachable only from the host. Keep it that way: MongoDB, Redis and the
  microservices have no authentication of their own unless you configure it,
  and MinIO keeps the default credentials Milvus is configured with.

Put the API behind a reverse proxy that terminates TLS (Nginx, Caddy,
Traefik) and set `API_BIND_ADDRESS=127.0.0.1` so it is reachable only
through the proxy. The production compose file starts uvicorn with
`--proxy-headers`; also set `FORWARDED_ALLOW_IPS=*` in `.env` so client
addresses are taken from the proxy's `X-Forwarded-For` header. Without it
the API sees every request coming from the Docker gateway, and the per-IP
login and registration limits turn into one limit shared by all users. Only
do this when the API port is bound to `127.0.0.1`, otherwise clients could
forge their address. Example Nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Request-ID $request_id;
    client_max_body_size 600m;          # PDF_MAX_SIZE_MB plus some margin
    proxy_buffering off;                # /jobs/stream (Server-Sent Events)
    proxy_read_timeout 1h;
}
```

## 4. Build and start

```bash
docker compose --profile tools build                      # analysis tool images (slow, once)
docker compose -f docker-compose-prod.yml up -d --scale workers=3
```

`docker-compose-prod.yml` runs the API without auto-reload and starts:

| Service | Role |
|---|---|
| `api` | FastAPI application. Its healthcheck is `GET /health/ready`. |
| `workers` | Celery workers running PDF extraction and analyses (each starts tool containers through the Docker socket). Scale with `--scale workers=N`; `WORKER_CONCURRENCY` sets tasks per worker. |
| `beat` | Celery beat: marks jobs abandoned by dead workers as failed (every 10 minutes) and reconciles storage usage with the disk (daily). Run exactly one. |
| `mongo`, `redis` | Database and task queue. |
| `cbir-service`, `milvus-standalone`, `etcd`, `minio` | Similarity search. |
| `provenance-service` | Provenance analysis. |
| `flower`, `attu` | Monitoring UIs, only with `--profile monitoring`. |

The workers mount `/var/run/docker.sock`, which gives them control of the
host's Docker daemon. Do not expose the workers or the Redis broker to
untrusted networks.

## 5. Create the first administrator

There is no default account. Create an administrator from the API container:

```bash
docker compose -f docker-compose-prod.yml exec api \
    python -m app.cli create-admin --username admin --email admin@example.org --generate-password
```

The generated password is printed once. The account is flagged
`must_change_password`; change the password after the first login
(`PUT /users/me/password` or the profile page).
To give an existing user the admin role:

```bash
docker compose -f docker-compose-prod.yml exec api python -m app.cli promote --username alice
```

## 6. Operate

- **Health**: `GET /health/live` (process up) and `GET /health/ready`
  (MongoDB and Redis reachable, `503` otherwise).
- **Logs**: `docker compose logs -f api workers beat`. Set `LOG_FORMAT=json`
  for log collectors. Every line carries a request ID, also returned to
  clients in the `X-Request-ID` header and stored on the jobs a request
  starts.
- **Audit**: administrator actions are listed by `GET /admin/audit-log`.
- **Storage**: usage is tracked per user and reconciled daily. To reconcile
  now (e.g. after restoring a backup):
  `docker compose -f docker-compose-prod.yml exec api python -m app.cli reconcile-storage`.

## 7. Back up and restore

Back up these together (stop the workers first, or accept that in-flight
jobs are lost):

| Data | Where | How |
|---|---|---|
| MongoDB | volume `mongo_data` | `docker compose exec mongo mongodump --archive --gzip > elies-$(date +%F).archive.gz` (add `--username/--password/--authenticationDatabase admin` if auth is on) |
| Workspace | `HOST_WORKSPACE_PATH` | Copy the directory (e.g. `rsync -a` or a filesystem snapshot). |
| Similarity index | volumes `milvus_data`, `milvus_etcd_data` and `milvus_minio_data` | Stop the CBIR stack and copy the volumes, or rebuild the index by re-indexing images (`POST /cbir/index`). |

Restore MongoDB with
`docker compose exec -T mongo mongorestore --archive --gzip --drop < elies-DATE.archive.gz`,
restore the workspace to the same path, then run `reconcile-storage`.

## 8. Upgrade

```bash
git pull && git submodule update --init
docker compose --profile tools build
docker compose -f docker-compose-prod.yml up -d --build
```

Indexes are created automatically when the API starts. Check the release
notes for configuration changes (compare your `.env` with `.env.example`).
