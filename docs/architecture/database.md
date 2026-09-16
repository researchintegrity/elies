# Database Schema (MongoDB)

This system uses MongoDB for flexible, document-first storage. Collections are created on demand and indexed at startup.

## Connection
- Default URL: `mongodb://localhost:27017`
- Default database: `elies_system`
- Override via environment: `MONGODB_URL`, `DATABASE_NAME`

## Collections (at a glance)
- `users`: Accounts, auth, and quota tracking.
- `documents`: Uploaded PDFs and extraction state.
- `images`: Extracted or uploaded images, panels, and analysis linkage.
- `annotations`: User-created image annotations.
- `analyses`: Analysis tasks/results (copy-move, CBIR, TruFor, provenance).

## Field Reference

### `users`
- `_id` (ObjectId)
- `username`, `email`, `hashed_password`, `full_name`
- `is_active` (bool)
- `storage_used_bytes`, `storage_limit_bytes`
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

## Indexes (created at startup)
- `users`: `username` (unique), `email` (unique)
- `documents`: `user_id`, `uploaded_date`, compound (`user_id`, `uploaded_date` desc)
- `images`: `user_id`, `document_id`, `uploaded_date`, `source_type`, compounds (`user_id`, `source_type`), (`document_id`, `source_type`)
- `annotations`: `user_id`, `image_id`, `created_at`, compounds (`user_id`, `image_id`), (`image_id`, `created_at` desc)
- `analyses`: `user_id`, `source_image_id`, `target_image_id`, `type`, `status`, `created_at`, compound (`user_id`, `created_at` desc)

## Relationships
- `documents.user_id` → `users._id`
- `images.user_id` → `users._id`
- `images.document_id` → `documents._id` (for extracted images)
- `annotations.image_id` → `images._id`
- `analyses.source_image_id` / `target_image_id` → `images._id`

## Operational Notes
- Collections are accessed via `app/db/mongodb.py`, which ensures indexes exist.
- All date fields use UTC (`datetime.utcnow`).
- Storage quotas are tracked per user (`storage_used_bytes` vs `storage_limit_bytes`).
- Analysis results are stored inline in `analyses.results` and summarized on related `images.analysis_status/results`.
