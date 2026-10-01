"""CBIR indexing failures keep images and record the error (#59)."""
import pytest
from bson import ObjectId
from celery.exceptions import Retry

import app.tasks.cbir as cbir_tasks
from app.db.mongodb import get_images_collection, get_indexing_jobs_collection, get_jobs_collection
from app.services.job_logger import create_job_log
from app.schemas import JobType


def _make_images(user_id, count):
    items = []
    for i in range(count):
        image_id = get_images_collection().insert_one(
            {"user_id": user_id, "filename": f"{i}.png", "file_path": f"/ws/{user_id}/{i}.png", "source_type": "uploaded"}
        ).inserted_id
        items.append({"image_id": str(image_id), "image_path": f"/ws/{user_id}/{i}.png", "labels": []})
    return items


def _run_with_progress(items, user_id="u1", retries=0):
    get_indexing_jobs_collection().insert_one({"_id": "idx_1", "user_id": user_id, "status": "pending"})
    main_job = create_job_log(user_id, JobType.BATCH_UPLOAD, "batch")
    task = cbir_tasks.cbir_index_batch_with_progress
    task.push_request(retries=retries)
    try:
        result = task.run(job_id="idx_1", user_id=user_id, image_items=items, main_job_id=main_job)
    finally:
        task.pop_request()
    return result, get_indexing_jobs_collection().find_one({"_id": "idx_1"}), get_jobs_collection().find_one({"_id": main_job})


def test_failed_indexing_keeps_images_and_records_error(mock_db, monkeypatch):
    items = _make_images("u1", 3)
    monkeypatch.setattr(cbir_tasks, "index_images_batch", lambda user_id, items: (False, "milvus down", {}))

    result, job, main_job = _run_with_progress(items)

    assert get_images_collection().count_documents({}) == 3
    for img in get_images_collection().find():
        assert img["cbir_indexed"] is False
        assert "milvus down" in img["cbir_error"]
    assert result["status"] == job["status"] == "failed"
    assert "kept in your gallery" in job["current_step"]
    assert main_job["status"] == "failed"


def test_partial_chunk_marks_each_image_individually(mock_db, monkeypatch):
    items = _make_images("u1", 2)
    monkeypatch.setattr(
        cbir_tasks, "index_images_batch",
        lambda user_id, cbir_items: (True, "ok", {"indexed_count": 1, "failed_count": 1}),
    )
    monkeypatch.setattr(
        cbir_tasks, "check_images_indexed",
        lambda user_id, paths: (True, "ok", {paths[0]: True, paths[1]: False}),
    )

    result, job, main_job = _run_with_progress(items)

    first = get_images_collection().find_one({"_id": ObjectId(items[0]["image_id"])})
    second = get_images_collection().find_one({"_id": ObjectId(items[1]["image_id"])})
    assert first["cbir_indexed"] is True and "cbir_error" not in first
    assert second["cbir_indexed"] is False
    assert job["status"] == "partial" and job["indexed_images"] == 1 and job["failed_images"] == 1
    assert main_job["status"] == "partial"


def test_successful_indexing_marks_all_images(mock_db, monkeypatch):
    items = _make_images("u1", 2)
    monkeypatch.setattr(
        cbir_tasks, "index_images_batch",
        lambda user_id, cbir_items: (True, "ok", {"indexed_count": 2, "failed_count": 0}),
    )
    result, job, main_job = _run_with_progress(items)
    assert all(img["cbir_indexed"] for img in get_images_collection().find())
    assert job["status"] == "completed" and main_job["status"] == "completed"


def test_cbir_outage_is_retried_then_images_are_kept(mock_db, monkeypatch):
    items = _make_images("u1", 2)
    monkeypatch.setattr(cbir_tasks, "check_cbir_health", lambda: (False, "connection refused"))

    with pytest.raises(Retry):
        _run_with_progress(items, retries=0)

    get_indexing_jobs_collection().delete_many({})
    result, job, _ = _run_with_progress(items, retries=cbir_tasks.cbir_index_batch_with_progress.max_retries)
    assert job["status"] == "failed"
    assert get_images_collection().count_documents({"cbir_indexed": False}) == 2


def test_extracted_images_batch_failure_does_not_delete(mock_db, monkeypatch):
    items = _make_images("u1", 2)
    get_images_collection().update_many({}, {"$set": {"source_type": "extracted"}})
    monkeypatch.setattr(cbir_tasks, "index_images_batch", lambda user_id, items: (False, "boom", {}))

    result = cbir_tasks.cbir_index_batch.run(user_id="u1", image_items=items)

    assert result["status"] == "failed"
    assert get_images_collection().count_documents({"cbir_indexed": False, "cbir_error": "boom"}) == 2
