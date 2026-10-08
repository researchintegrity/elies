# ELIES API Reference

This guide explains how to use the ELIES API: authentication, conventions and
the main workflows. Two references are generated from the code and are always
complete:

- the **interactive reference** served by the API: `/docs` (Swagger UI) and
  `/redoc`, with every request and response schema;
- the **endpoint list** in [docs/api/endpoints.md](docs/api/endpoints.md),
  regenerated with `python tools/generate_api_reference.py` (a unit test
  fails when it is out of date).

All paths below are relative to the API root (`http://localhost:8000` by
default). There is no `/api/v1` prefix.

---

## Authentication

### Register and log in

```http
POST /auth/register
Content-Type: application/json

{"username": "alice", "email": "alice@example.org", "password": "a-long-passphrase", "full_name": "Alice"}
```

```http
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=alice&password=a-long-passphrase
```

Both return `{"access_token": "...", "token_type": "bearer", "user": {...}}`.
`username` may also be the account's email address. Registering a username or
email that is already taken is `400`. Passwords need at least
`PASSWORD_MIN_LENGTH` characters (12 by default) and at most 72 bytes.

Send the token on every other request:

```http
Authorization: Bearer <access_token>
```

Media URLs used directly in `<img>` tags (`/images/{id}/thumbnail`,
`/images/{id}/download`, `/analyses/{id}/results/{type}/download`) also accept
the token as a `?token=` query parameter. Tokens are redacted from access logs.

### Token lifetime and revocation

Tokens expire after `JWT_EXPIRATION_HOURS` (24 by default). They are revoked
immediately when the user changes their password, deletes their account, or
when an administrator changes their roles, resets their password or
deactivates them. A revoked token gets `401`.

```http
PUT /users/me/password
{"current_password": "...", "new_password": "..."}
```

returns a fresh token for the session that made the change.

### Limits

Repeated failed logins (per account and per IP) and registrations (per IP) are
rate limited: the API answers `429` with a `Retry-After` header.

### Administrators

There is no default administrator. Create the first one from the server:

```bash
docker compose exec api python -m app.cli create-admin --username admin --email admin@example.org --generate-password
docker compose exec api python -m app.cli promote --username alice   # make an existing user admin
```

Admin endpoints live under `/admin` (user listing, quota, roles, password
reset, activation, deletion, statistics and the audit log).

---

## Conventions

| Topic | Rule |
|---|---|
| IDs | MongoDB ObjectIds as 24-character hex strings. A malformed ID is `400`. |
| Ownership | Users only see their own resources; someone else's resource is `404`. |
| Dates | ISO 8601 in UTC with an explicit offset, e.g. `2026-10-01T12:00:00Z`. |
| Errors | `{"detail": "message"}`; validation errors (`422`) are `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}`. Unexpected errors are a generic `500` (details only in the server log). |
| Request IDs | Every response has an `X-Request-ID` header. Send your own (letters, digits, `.`, `_`, `-`, up to 64) to correlate logs; it is stored on the jobs your request starts. |
| Pagination | List endpoints take `page` (from 1) and `per_page`, and return the total and page count. |
| Asynchronous work | Long operations answer `202 Accepted` and create a *job* (see [Jobs](#jobs)) and, for analyses, an *analysis* record to poll. |
| Busy users | A user may have `MAX_ACTIVE_JOBS_PER_USER` (20) analysis jobs waiting or running; more are refused with `429`. |
| Queue outage | If the task queue is down, endpoints that start work answer `503` and mark the job failed. |

### Status codes

| Code | Meaning |
|---|---|
| 200 / 201 / 204 | Success / created / deleted |
| 202 | Accepted: the work continues in the background |
| 400 | Invalid input (bad ID, file type, content) |
| 401 | Missing, invalid, expired or revoked token |
| 403 | Authenticated but not allowed (e.g. not an admin) |
| 404 | Not found, or not yours |
| 409 | Conflict with the current state of a resource |
| 413 | Storage quota exceeded |
| 422 | Request body or parameters fail validation |
| 429 | Rate limited or too many active jobs |
| 503 | A dependency (database, task queue, CBIR, Docker) is unavailable: retry later |

### Storage quota

Each user has a quota (`DEFAULT_USER_STORAGE_QUOTA_GB`, 1 GB by default;
admins can change it per user). Everything stored in the user's workspace
counts: uploads, extracted images, panels, thumbnails, analysis results and
watermark-removed PDFs. Uploads are refused with `413` when they do not fit.
Upload and list responses include `user_storage_used` and
`user_storage_remaining`.

---

## Documents (PDF)

```http
POST /documents/upload            multipart/form-data, field "file"
GET  /documents?page=1&per_page=12
GET  /documents/{doc_id}
GET  /documents/{doc_id}/images
GET  /documents/{doc_id}/download
DELETE /documents/{doc_id}
```

Uploading a PDF (max `PDF_MAX_SIZE_MB`, 500 MB by default) queues image
extraction. Poll `GET /documents/{doc_id}` until `extraction_status` is
`completed`, `completed_with_errors` or `failed`; `extracted_image_count`
gives the result. Uploads are refused with `503` while CBIR is down, because
the extracted images could not be indexed.

Deleting a document deletes its extracted images and everything derived from
them (panels, annotations, relationships, analyses, CBIR vectors).

### Watermark removal

```http
POST /documents/{doc_id}/remove-watermark
{"aggressiveness_mode": 2}

GET /documents/{doc_id}/watermark-removal/status
```

Modes: `1` explicit watermarks, `2` text and repeated graphics, `3` all
graphics. The cleaned PDF becomes a new document (`cleaned_document_id`).

---

## Images

```http
POST /images/upload?document_id=<optional>   multipart/form-data, field "file"
POST /images/upload/batch                     multipart/form-data, fields "files"
GET  /images/indexing-status/{job_id}         progress of a batch upload's indexing
GET  /images?page=1&per_page=24&source_type=uploaded&flagged=true&search=fig
GET  /images/ids                              IDs matching the same filters (select all)
GET  /images/{image_id}
GET  /images/{image_id}/thumbnail
GET  /images/{image_id}/download
PATCH /images/{image_id}/flag                 toggle "flagged for review"
POST /images/{image_id}/types                 {"types": ["figure"]}
DELETE /images/{image_id}/types/{type_name}
GET  /images/tags                             all types used by the user
DELETE /images/{image_id}
```

Accepted formats: PNG, JPEG, GIF, BMP, WebP, up to `IMAGE_MAX_SIZE_MB`
(100 MB) and `MAX_IMAGE_PIXELS` pixels; the content must decode as an image.
The client file name is kept only for display (`original_filename`).
Images extracted from a PDF can only be deleted with their document (`403`).

### Panel extraction

```http
POST /images/extract-panels
{"image_ids": ["<image id>", "..."], "model_type": "default"}

GET /images/extract-panels/status/{task_id}
GET /images/{image_id}/panels
```

At most `MAX_IMAGES_PER_EXTRACTION` (20) images per request. The status is
`queued`, `processing`, `completed` (with `extracted_panels`) or `failed`
(with `error`). Only the user who started an extraction can read its status.

---

## Analyses

Every analysis is stored as an analysis record. Start one, then poll
`GET /analyses/{analysis_id}` (or follow its job) until `status` is
`completed` or `failed`.

```http
POST /analyses/copy-move/single   {"image_id": "...", "method": "keypoint"}
POST /analyses/copy-move/cross    {"source_image_id": "...", "target_image_id": "...", "method": "keypoint", "descriptor": "cv_rsift"}
POST /analyses/trufor             {"image_id": "...", "save_noiseprint": false}
POST /analyses/screening-tool     multipart: image_id, analysis_subtype, parameters (JSON object), notes, result_image (optional image)

GET  /analyses?page=1&per_page=10&type=trufor&status=completed&source_image_id=...
GET  /analyses/stats
GET  /analyses/by-image/{image_id}
GET  /analyses/{analysis_id}
GET  /analyses/{analysis_id}/results/{result_type}/download
DELETE /analyses/{analysis_id}
```

Copy-move `method` is `keypoint` (recommended) or `dense` (with
`dense_method` 1-5). Single-image copy-move also accepts `forgeryscope`,
which finds duplicated panels, panel regions and western blot lanes in a
figure. Its results add `verdict` (`authentic` or `duplicated`),
`detections` (one entry per group of duplicated regions: panel type, panel
ids, similarity, bounding box, matched pairs or lanes) and `panels` (the
detected panels); the `matches` and `clusters` images exist only when
duplication is found, and `report` downloads the full JSON report. Screening-tool results come from the browser tools (ELA,
noise analysis, ...) and are stored as completed analyses; the optional
result image is validated and counted in the quota.

### Content-based image retrieval (CBIR)

```http
POST /cbir/index          {"image_ids": ["..."], "labels": ["Western Blot"]}   (all images if image_ids is omitted)
POST /cbir/search         {"image_id": "...", "top_k": 10, "labels": ["..."]}  -> analysis_id
POST /cbir/search/sync    same body, answers with the matches
POST /cbir/search/upload?top_k=10   multipart "file": search with an image that is not stored
DELETE /cbir/index        {"image_ids": ["..."]}
DELETE /cbir/index/all
GET  /cbir/health
```

Uploaded and extracted images are indexed automatically. If indexing fails
the image is kept and marked with `cbir_error`; it is never deleted.

### Provenance analysis

```http
POST /provenance/analyze
{"image_id": "...", "search_image_ids": null, "k": 10, "q": 5, "max_depth": 3, "descriptor_type": "cv_rsift"}

GET /provenance/health
```

Answers `202` with `analysis_id`; the results (provenance graph and spanning
tree) are read with `GET /analyses/{analysis_id}`. The edges found are also
stored as image relationships (`source_type: "provenance"`).

---

## Relationships

```http
POST   /relationships                         {"image1_id": "...", "image2_id": "...", "source_type": "manual", "weight": 1.0}
GET    /relationships/image/{image_id}        direct relationships, with the other image's details
GET    /relationships/image/{image_id}/graph?max_depth=5
DELETE /relationships/{relationship_id}
```

Relationships are undirected and unique per image pair: creating an existing
one returns it (raising its weight if the new one is higher). Both images are
flagged when a relationship is created.

The graph endpoint returns `nodes`, `edges` and the maximum spanning tree
(`mst_edges`, also marked with `is_mst_edge`). `max_depth=0` means unlimited.
`total_nodes_count` is the size of the connected component, explored up to
`RELATIONSHIP_GRAPH_MAX_NODES` (2000) images; `truncated` is `true` when that
cap was reached.

---

## Annotations

```http
POST /annotations/single                 {"image_id": "...", "coords": {...}, "text": "", "type": "manipulation", "shape_type": "rectangle"}
GET  /annotations/single?image_id=...
GET  /annotations/single/{annotation_id}
DELETE /annotations/single/{annotation_id}

POST /annotations/dual                   {"source_image_id": "...", "target_image_id": "...", "link_id": "...", "coords": {...}}
POST /annotations/dual/batch             [ ...same objects... ]
GET  /annotations/dual?source_image_id=...
GET  /annotations/dual/linked-images/{image_id}
GET|PUT|DELETE /annotations/dual/{annotation_id}
PUT|DELETE /annotations/dual/by-link/{link_id}
```

Single annotations mark a region of one image; dual annotations link a region
of one image to a region of another (both sides share a `link_id`).

---

## Jobs

Every background operation (PDF extraction, analyses, panel extraction,
watermark removal, batch uploads, deletions) has a job record.

```http
GET /jobs?page=1&per_page=20&job_type=trufor&status=processing
GET /jobs/stats
GET /jobs/{job_id}
GET /jobs/stream            Server-Sent Events
```

Job statuses: `pending`, `processing`, `completed`, `partial` (completed with
errors) and `failed`. Jobs are deleted `JOB_RETENTION_DAYS` after they finish.
Jobs whose worker died are marked failed automatically.

`/jobs/stream` sends one `data: {...}` event per change (`job_started`,
`job_progress`, `job_completed`, `job_failed`) and a `: keepalive` comment
every 30 seconds. Browsers' `EventSource` cannot send headers, so read it
with `fetch` and the `Authorization` header.

---

## Users

```http
GET    /users/me
PUT    /users/me                  {"full_name": "...", "email": "..."}
PUT    /users/me/password         {"current_password": "...", "new_password": "..."}
DELETE /users/me                  disables the account at once, deletes its data in the background
GET    /users/{username}          admins only
```

---

## Health

```http
GET /health/live     200 while the process runs
GET /health/ready    200 when MongoDB and Redis are reachable, 503 otherwise
GET /health          same as /health/ready
```

Readiness answers never include error details; see the server log.

---

## Deprecated endpoints

The `/api/*` routes (`/api/documents`, `/api/images`, `/api/search`,
`/api/dashboard/stats`, `/api/health`) duplicate the endpoints above with a
different response envelope. They still work, answer with a
`Deprecation: true` header and will be removed. `GET /images/types/all` is
replaced by `GET /images/tags`.

---

## Example workflow

```bash
API=http://localhost:8000

# Register (or log in) and keep the token
TOKEN=$(curl -s -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"username":"alice","email":"alice@example.org","password":"a-long-passphrase"}' | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"

# Upload a PDF and wait for its images
DOC=$(curl -s -X POST $API/documents/upload -H "$AUTH" -F file=@paper.pdf | jq -r ._id)
curl -s $API/documents/$DOC -H "$AUTH" | jq .extraction_status
curl -s "$API/documents/$DOC/images" -H "$AUTH" | jq '.[]._id'

# Run TruFor on one of them and poll the analysis
ANALYSIS=$(curl -s -X POST $API/analyses/trufor -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"image_id\":\"$IMAGE\"}" | jq -r .analysis_id)
curl -s $API/analyses/$ANALYSIS -H "$AUTH" | jq .status
```
