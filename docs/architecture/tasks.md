# Background tasks

Long-running work runs in Celery workers (`app/tasks/`), with Redis as the
broker. The API records a **job** for each piece of work (`/jobs`, live
updates on `/jobs/stream`) and, for analyses, an **analysis** record.

## Main flows

| Trigger | Task | Then |
|---|---|---|
| PDF upload | `tasks.extract_images` (pdf-extractor container) | extracted images are registered and `tasks.cbir_index_batch` indexes them |
| Image upload | `tasks.cbir_index_image` / batch indexing with progress | |
| `POST /images/extract-panels` | `tasks.extract_panels` (panel-extractor) | panels are registered as images and indexed |
| `POST /analyses/copy-move/*` | `tasks.detect_copy_move`, `tasks.detect_copy_move_cross` | results stored on the analysis |
| `POST /analyses/trufor` | `tasks.detect_trufor` | results stored on the analysis |
| `POST /cbir/search` | `tasks.cbir_search` | matches stored on the analysis |
| `POST /provenance/analyze` | `tasks.provenance_analysis` | graph stored on the analysis, edges stored as relationships |
| `POST /documents/{id}/remove-watermark` | `tasks.remove_watermark` | the cleaned PDF becomes a new document |
| `DELETE /users/me`, admin deletion | `tasks.delete_user_account` | |

Analysis tools run in their own containers through the Docker CLI
(`app/utils/docker_runner.py`): read-only inputs, no network, a PID limit,
optional CPU/memory limits, and the container is killed when its timeout
expires. Containers left behind by a crashed worker are killed when a worker
starts.

## Failures and retries

`app/tasks/lifecycle.py` gives every task the same rules:

- only transient infrastructure errors (Docker daemon, MongoDB, CBIR or
  provenance unreachable) are retried, with exponential backoff (30 s, 60 s,
  120 s ... up to 10 minutes, `CELERY_MAX_RETRIES` times);
- a job or analysis is marked failed only once no retry will follow;
- a task that hits the Celery soft time limit fails without retrying;
- any other exception fails the task.

PDF extraction is idempotent: a retried or redelivered task discards what an
interrupted attempt registered. If CBIR indexing fails, images are kept and
marked with `cbir_error`.

If the broker is unreachable when work is submitted, the API marks the job
and analysis failed and answers `503`.

## Periodic tasks (Celery beat)

The `beat` service runs:

- `tasks.reap_stale_jobs` every 10 minutes: jobs, analyses, indexing jobs and
  documents left `processing` for `STALE_PROCESSING_MINUTES` or `pending` for
  `STALE_PENDING_HOURS` belong to a worker that died and are marked failed;
- `tasks.reconcile_storage` daily: recomputes every user's storage usage
  from disk.

## Job events

Workers publish job events on Redis (channel `elies:jobs:<user_id>`); the API
relays them to the user's `/jobs/stream` connections. Tasks carry the
`X-Request-ID` of the request that queued them, so their log lines can be
matched with the API's.
