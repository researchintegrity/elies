# Database Schema (MongoDB)

This system uses MongoDB for flexible, document-first storage. Collections are created on demand; their indexes are declared in `INDEXES` (`app/db/mongodb.py`) and created once per process when it connects (API startup, a worker's first database access).

## Connection
- Default URL: `mongodb://localhost:27017`
- Default database: `elies_system`
- Override via environment: `MONGODB_URL`, `DATABASE_NAME`

## Collections (at a glance)
- `users`: Accounts, auth, and quota tracking.
- `documents`: Uploaded PDFs and extraction state.
- `images`: Extracted or uploaded images, panels, and analysis linkage.
- `single_annotations`, `dual_annotations`: User-created annotations on one image, or linking regions of two images.
- `analyses`: Analysis tasks/results (copy-move, CBIR, TruFor, provenance, screening tools).
- `image_relationships`: Undirected relationships between two images (unique per pair).
- `jobs`: Background job log shown on the jobs dashboard (expires through a TTL index).
- `indexing_jobs`: Progress of batch CBIR indexing.
- `admin_audit_log`: Administrator actions.

## Field Reference

### `users`
- `_id` (ObjectId)
- `username`, `email`, `hashed_password`, `full_name`
- `is_active` (bool)
- `roles` (`user`, `admin`), `token_version` (incremented to revoke tokens), `must_change_password`
- `storage_used_bytes` (running total of the user's workspace), `storage_limit_bytes`
- `created_at`, `updated_at`

### `documents`
- `_id` (ObjectId)
- `user_id` (str, FK to `users`)
- `filename`, `file_path`, `file_size`
- `extraction_status` (`pending|completed|failed`)
- `extracted_image_count`, `extraction_errors` (list)
- `uploaded_date`
- Derived per-request: `user_storage_used`, `user_storage_remaining`

### `images`
- `_id` (ObjectId)
- `user_id` (str)
- `filename`, `file_path`, `file_size`
- `source_type` (`extracted|uploaded|panel`)
- `document_id` (str, optional)
- Panel fields (when `source_type=panel`): `source_image_id`, `panel_id`, `panel_type`, `bbox`
- PDF extraction metadata: `pdf_page`, `page_bbox`, `extraction_mode`, `original_filename`
- `image_type` (list[str], user-editable labels)
- Analysis linkage: `analysis_status` (map), `analysis_results` (map), `analysis_ids` (list)
- `exif_metadata` (dict, optional)
- `uploaded_date`
- Derived per-request: `user_storage_used`, `user_storage_remaining`

### `annotations`
- `_id` (ObjectId)
- `user_id`, `image_id`
- `text`
- `coords` (`x`, `y`, `width`, `height` in percentages)
- `created_at`, `updated_at`

### `analyses`
- `_id` (ObjectId)
- `type` (`single_image_copy_move`, `cross_image_copy_move`, `trufor`, `cbir_search`, `provenance`)
- `user_id`
- `source_image_id`, `target_image_id` (optional for cross-image)
- `status` (`pending|processing|completed|failed`)
- `results` (method-specific payload; may include CBIR matches, visualization paths, logs)
- `error` (optional)
- `created_at`, `updated_at`

## Indexes

Declared in `INDEXES` in `app/db/mongodb.py`. Highlights: unique `username`
and `email` on `users`; unique (`user_id`, `image1_id`, `image2_id`) on
`image_relationships`; a TTL index on `jobs.expires_at`; compound indexes for
the dashboards' (`user_id`, ..., `created_at`) queries.

## Relationships
- `documents.user_id` → `users._id`
- `images.user_id` → `users._id`
- `images.document_id` → `documents._id` (for extracted images)
- `annotations.image_id` → `images._id`
- `analyses.source_image_id` / `target_image_id` → `images._id`

## Operational Notes
- Collections are accessed via the getters in `app/db/mongodb.py`.
- All dates are timezone-aware UTC (the client is created with `tz_aware=True`).
- Storage quotas are tracked per user: uploads reserve space with a conditional `$inc` on `storage_used_bytes`, deletions release it, and a daily task reconciles it with the disk.
- Analysis results are stored inline in `analyses.results` and summarized on related `images.analysis_status/results`.
