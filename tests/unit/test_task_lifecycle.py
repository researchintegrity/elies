"""Task lifecycle (#68), PDF extraction idempotency (#61) and worker events over Redis (#64)."""
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from bson import ObjectId
from celery.app.task import Task
from celery.exceptions import Retry, SoftTimeLimitExceeded

import app.tasks.image_extraction as extraction_task
import app.tasks.trufor as trufor_task
from app.config.settings import MAX_ACTIVE_JOBS_PER_USER, UPLOAD_DIR
from app.db.mongodb import (
    get_analyses_collection,
    get_documents_collection,
    get_images_collection,
    get_indexing_jobs_collection,
    get_jobs_collection,
)
from app.exceptions import DockerUnavailableError
from app.routes import jobs as jobs_routes
from app.schemas import JobType
from app.services import job_logger
from app.utils import redis_client
from app.tasks.maintenance import reap_stale_jobs
from tests.unit.conftest import PDF_BYTES, PNG_BYTES


def _run(task, retries=0, **kwargs):
    task.push_request(retries=retries, id="celery-task-1", called_directly=False)
    try:
        return task.run(**kwargs)
    finally:
        task.pop_request()


# ------------------------------------------------------------ #61 -------

@pytest.fixture
def document(client, alice):
    body = client.post("/documents/upload", headers=alice.headers,
                       files={"file": ("paper.pdf", PDF_BYTES, "application/pdf")}).json()
    job = get_jobs_collection().find_one({"job_type": "image_extraction"})
    return {"id": body["_id"], "path": body["file_path"], "user": alice.id, "job_id": job["_id"]}


def _fake_extractor(monkeypatch, filenames=(), error=None):
    calls = []

    def hook(doc_id, user_id, pdf_file_path):
        calls.append(doc_id)
        if error:
            raise error
        out = UPLOAD_DIR / user_id / "images" / "extracted" / doc_id
        out.mkdir(parents=True, exist_ok=True)
        files = []
        for name in filenames:
            (out / name).write_bytes(PNG_BYTES)
            files.append({"filename": name, "path": str(out / name), "size": len(PNG_BYTES), "mime_type": "image/png"})
        return len(files), [], files

    monkeypatch.setattr(extraction_task, "figure_extraction_hook", hook)
    return calls


def _extract(document, retries=0):
    return _run(extraction_task.extract_images_from_document, retries=retries,
                doc_id=document["id"], user_id=document["user"], pdf_path=document["path"], job_id=document["job_id"])


def test_pdf_without_images_completes_once(monkeypatch, document, celery_calls):
    calls = _fake_extractor(monkeypatch)
    result = _extract(document)

    assert result["status"] == "completed" and result["extracted_count"] == 0
    assert calls == [document["id"]]
    assert get_documents_collection().find_one({"_id": ObjectId(document["id"])})["extraction_status"] == "completed"
    assert get_jobs_collection().find_one({"_id": document["job_id"]})["status"] == "completed"


def test_extracted_images_are_registered_and_indexed(monkeypatch, document, celery_calls):
    _fake_extractor(monkeypatch, ["p-1-x0-1.0-y0-2.0-x1-3.0-y1-4.0-1.png", "p-2-1.png"])
    result = _extract(document)

    images = list(get_images_collection().find({"document_id": document["id"]}))
    assert result["extracted_count"] == len(images) == 2
    for img in images:
        assert Path(img["file_path"]).name == f"{img['_id']}.png" and Path(img["file_path"]).exists()
    assert {img["pdf_page"] for img in images} == {1, 2}
    assert any(c.name == "tasks.cbir_index_batch" for c in celery_calls)


def test_retry_starts_from_a_clean_slate(monkeypatch, document):
    out = UPLOAD_DIR / document["user"] / "images" / "extracted" / document["id"]
    leftover = out / f"{ObjectId()}.png"
    leftover.write_bytes(PNG_BYTES)
    get_images_collection().insert_one({"user_id": document["user"], "document_id": document["id"],
                                        "source_type": "extracted", "file_path": str(leftover)})
    _fake_extractor(monkeypatch, ["p-1-1.png"])

    _extract(document, retries=1)

    assert get_images_collection().count_documents({"document_id": document["id"]}) == 1
    assert not leftover.exists()


def test_redelivered_task_for_finished_document_does_nothing(monkeypatch, document):
    _fake_extractor(monkeypatch, ["p-1-1.png"])
    _extract(document)
    calls = _fake_extractor(monkeypatch, ["p-1-1.png"])
    _extract(document)
    assert calls == []
    assert get_images_collection().count_documents({"document_id": document["id"]}) == 1


def test_docker_outage_is_retried_without_failing_the_job(monkeypatch, document):
    _fake_extractor(monkeypatch, error=DockerUnavailableError("daemon down"))

    with pytest.raises(Retry):
        _extract(document)
    assert get_jobs_collection().find_one({"_id": document["job_id"]})["status"] == "processing"

    with pytest.raises(DockerUnavailableError):
        _extract(document, retries=extraction_task.extract_images_from_document.max_retries)
    assert get_jobs_collection().find_one({"_id": document["job_id"]})["status"] == "failed"
    assert get_documents_collection().find_one({"_id": ObjectId(document["id"])})["extraction_status"] == "failed"


def test_permanent_errors_are_not_retried(monkeypatch, document):
    _fake_extractor(monkeypatch, error=ValueError("corrupt PDF"))
    with pytest.raises(ValueError):
        _extract(document)
    assert get_jobs_collection().find_one({"_id": document["job_id"]})["status"] == "failed"


# ------------------------------------------------------------ #68 -------

@pytest.fixture
def trufor_analysis(client, alice):
    image_id = client.post("/images/upload", headers=alice.headers,
                           files={"file": ("a.png", PNG_BYTES, "image/png")}).json()["_id"]
    analysis_id = client.post("/analyses/trufor", headers=alice.headers, json={"image_id": image_id}).json()["analysis_id"]
    job = get_jobs_collection().find_one({"job_type": "trufor"})
    return {"analysis_id": analysis_id, "image_id": image_id, "user": alice.id, "job_id": job["_id"]}


def _run_trufor(analysis, retries=0):
    return _run(trufor_task.detect_trufor, retries=retries, analysis_id=analysis["analysis_id"],
                image_id=analysis["image_id"], user_id=analysis["user"], image_path="/x.png", job_id=analysis["job_id"])


def _status(analysis):
    a = get_analyses_collection().find_one({"_id": ObjectId(analysis["analysis_id"])})
    j = get_jobs_collection().find_one({"_id": analysis["job_id"]})
    return a["status"], j["status"]


def test_analysis_job_has_task_id(trufor_analysis):
    assert get_jobs_collection().find_one({"_id": trufor_analysis["job_id"]})["celery_task_id"]


def test_transient_failure_retries_without_marking_failed(monkeypatch, trufor_analysis):
    def unavailable(**kwargs):
        raise DockerUnavailableError("daemon down")

    monkeypatch.setattr(trufor_task, "run_trufor_detection_with_docker", unavailable)
    with pytest.raises(Retry):
        _run_trufor(trufor_analysis)
    assert _status(trufor_analysis) == ("processing", "processing")


@pytest.mark.parametrize("error", [SoftTimeLimitExceeded(), KeyError("bug")])
def test_time_limits_and_bugs_fail_without_retry(monkeypatch, trufor_analysis, error):
    def broken(**kwargs):
        raise error

    monkeypatch.setattr(trufor_task, "run_trufor_detection_with_docker", broken)
    with pytest.raises(type(error)):
        _run_trufor(trufor_analysis)
    assert _status(trufor_analysis) == ("failed", "failed")


def test_tool_failure_and_success_are_recorded(monkeypatch, trufor_analysis):
    monkeypatch.setattr(trufor_task, "run_trufor_detection_with_docker",
                        lambda **kw: (True, "ok", {"pred_map": "/p.png", "conf_map": "/c.png"}))
    _run_trufor(trufor_analysis)
    assert _status(trufor_analysis) == ("completed", "completed")


def test_broker_outage_fails_job_and_analysis(client, alice, monkeypatch):
    image_id = client.post("/images/upload", headers=alice.headers,
                           files={"file": ("a.png", PNG_BYTES, "image/png")}).json()["_id"]

    def broken(self, *args, **kwargs):
        raise ConnectionError("broker down")

    monkeypatch.setattr(Task, "apply_async", broken)
    response = client.post("/analyses/trufor", headers=alice.headers, json={"image_id": image_id})

    assert response.status_code == 503
    assert get_analyses_collection().find_one()["status"] == "failed"
    assert get_jobs_collection().find_one({"job_type": "trufor"})["status"] == "failed"


def test_active_job_limit(client, alice):
    image_id = client.post("/images/upload", headers=alice.headers,
                           files={"file": ("a.png", PNG_BYTES, "image/png")}).json()["_id"]
    for _ in range(MAX_ACTIVE_JOBS_PER_USER):
        job_logger.create_job_log(alice.id, JobType.TRUFOR, "queued")
    response = client.post("/analyses/trufor", headers=alice.headers, json={"image_id": image_id})
    assert response.status_code == 429


def test_reaper_fails_only_stale_work(mock_db):
    now = datetime.now(timezone.utc)
    old, fresh = now - timedelta(hours=2), now - timedelta(minutes=5)
    stale_job = job_logger.create_job_log("u1", JobType.TRUFOR, "stale")
    get_jobs_collection().update_one({"_id": stale_job}, {"$set": {"status": "processing", "updated_at": old}})
    live_job = job_logger.create_job_log("u1", JobType.TRUFOR, "live")
    get_jobs_collection().update_one({"_id": live_job}, {"$set": {"status": "processing", "updated_at": fresh}})
    queued_job = job_logger.create_job_log("u1", JobType.TRUFOR, "queued")
    get_analyses_collection().insert_one({"user_id": "u1", "status": "processing", "updated_at": old})
    get_indexing_jobs_collection().insert_one({"_id": "idx", "user_id": "u1", "status": "processing", "updated_at": old})
    get_documents_collection().insert_one({"user_id": "u1", "extraction_status": "processing", "extraction_started_at": old})

    counts = reap_stale_jobs(now)

    assert counts == {"jobs": 1, "analyses": 1, "indexing_jobs": 1, "documents": 1}
    assert get_jobs_collection().find_one({"_id": stale_job})["status"] == "failed"
    assert get_jobs_collection().find_one({"_id": live_job})["status"] == "processing"
    assert get_jobs_collection().find_one({"_id": queued_job})["status"] == "pending"


def test_new_jobs_expire_even_if_never_completed(mock_db):
    job_id = job_logger.create_job_log("u1", JobType.TRUFOR, "t")
    assert get_jobs_collection().find_one({"_id": job_id})["expires_at"] > datetime.now(timezone.utc)


def test_job_stats_count_partial(client, alice):
    job_id = job_logger.create_job_log(alice.id, JobType.BATCH_UPLOAD, "b")
    job_logger.complete_job(job_id, alice.id, job_logger.JobStatus.PARTIAL, errors=["1 failed"])
    assert client.get("/jobs/stats", headers=alice.headers).json()["partial"] == 1


# ------------------------------------------------------------ #64 -------

async def _next_data_event(stream, timeout=5.0):
    async def read():
        async for chunk in stream:
            if chunk.startswith("data:"):
                return chunk
    return await asyncio.wait_for(read(), timeout)


def test_events_from_another_process_reach_the_stream(mock_db):
    job_id = job_logger.create_job_log("u1", JobType.TRUFOR, "t")

    async def scenario():
        stream = jobs_routes.job_event_stream("u1")
        waiter = asyncio.ensure_future(_next_data_event(stream))
        await asyncio.sleep(0.2)  # let the stream subscribe
        # A Celery worker publishes from its own thread/process
        worker = threading.Thread(target=job_logger.update_job_progress, args=(job_id, "u1", None, 50, "halfway"))
        worker.start()
        worker.join()
        chunk = await waiter
        await stream.aclose()
        return chunk

    chunk = asyncio.run(scenario())
    assert '"event": "job_progress"' in chunk and '"job_type": "trufor"' in chunk and "halfway" in chunk


def test_stream_falls_back_to_in_process_events_without_redis(mock_db, monkeypatch):
    import redis

    class DownPubSub:
        async def subscribe(self, *args):
            raise redis.ConnectionError("down")

    class DownClient:
        def pubsub(self):
            return DownPubSub()

        async def aclose(self):
            pass

    monkeypatch.setattr(jobs_routes, "_async_redis_client", lambda: DownClient())
    monkeypatch.setattr(redis_client, "_unavailable_until", float("inf"))
    job_id = job_logger.create_job_log("u1", JobType.TRUFOR, "t")

    async def scenario():
        stream = jobs_routes.job_event_stream("u1")
        waiter = asyncio.ensure_future(_next_data_event(stream))
        await asyncio.sleep(0.2)
        await asyncio.to_thread(job_logger.complete_job, job_id, "u1", job_logger.JobStatus.COMPLETED)
        chunk = await waiter
        await stream.aclose()
        return chunk

    assert '"event": "job_completed"' in asyncio.run(scenario())


def test_retry_is_scheduled_even_if_mongodb_is_the_service_that_is_down(mock_db, monkeypatch):
    from pymongo.errors import ServerSelectionTimeoutError

    from app.tasks.lifecycle import TrackedJob, handle_task_exception

    class Job(TrackedJob):
        def progress(self, percent, message):
            raise ServerSelectionTimeoutError("no servers")

    task = trufor_task.detect_trufor
    task.push_request(retries=0, id="celery-task-1", called_directly=False)
    try:
        with pytest.raises(Retry):
            handle_task_exception(task, ServerSelectionTimeoutError("no servers"), Job("u1", None, None))
    finally:
        task.pop_request()


def test_cbir_deletion_is_retried_while_cbir_is_unreachable(mock_db, monkeypatch):
    import requests

    import app.utils.docker_cbir as docker_cbir
    from app.exceptions import TransientError

    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(docker_cbir.requests, "post", unreachable)
    with pytest.raises(TransientError):
        docker_cbir.delete_user_data("u1")
    with pytest.raises(TransientError):
        docker_cbir.delete_image_from_index("u1", str(UPLOAD_DIR / "u1" / "x.png"))

    import app.tasks.cbir as cbir_tasks

    task = cbir_tasks.cbir_delete_user_data
    task.push_request(retries=0, id="celery-task-2", called_directly=False)
    try:
        with pytest.raises(Retry):
            task.run(user_id="u1")
    finally:
        task.pop_request()
