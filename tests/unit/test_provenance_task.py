"""Provenance service and task (moved from tests/test_provenance_integration.py)."""
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from app.db.mongodb import get_analyses_collection, get_jobs_collection
from app.services.provenance_service import run_provenance_analysis
from app.tasks.provenance import provenance_analysis_task


@pytest.fixture
def mock_analyze_provenance():
    with patch("app.services.provenance_service.analyze_provenance") as mock_analyze:
        yield mock_analyze


def test_run_provenance_analysis_passes_query_and_candidates(mock_analyze_provenance):
    images = MagicMock()
    images.find_one.return_value = {"_id": "507f1f77bcf86cd799439011", "file_path": "/path/to/query.jpg",
                                    "filename": "query.jpg", "user_id": "user123"}
    images.find.return_value = [
        {"_id": "507f1f77bcf86cd799439011", "file_path": "/path/to/query.jpg", "filename": "query.jpg"},
        {"_id": "507f1f77bcf86cd799439012", "file_path": "/path/to/other.jpg", "filename": "other.jpg"},
    ]
    mock_analyze_provenance.return_value = (True, "Success", {"graph": "data"})

    with patch("app.services.provenance_service.get_images_collection", return_value=images):
        success, _, result = run_provenance_analysis("user123", "507f1f77bcf86cd799439011")

    assert success is True and result == {"graph": "data"}
    call_args = mock_analyze_provenance.call_args.kwargs
    assert call_args["user_id"] == "user123"
    assert call_args["query_image"]["path"] == "/path/to/query.jpg"
    assert len(call_args["images"]) == 2


def test_provenance_task_completes_the_analysis_and_creates_relationships(mock_db):
    analysis_id = str(get_analyses_collection().insert_one({"user_id": "u1", "status": "pending"}).inserted_id)
    a, b = str(ObjectId()), str(ObjectId())
    graph = {"graph": {"edges": [{"from": a, "to": b, "weight": 0.8}]}}

    with patch("app.tasks.provenance.run_provenance_analysis", return_value=(True, "Success", graph)):
        result = provenance_analysis_task.run(analysis_id=analysis_id, user_id="u1", query_image_id=a)

    assert result["status"] == "completed"
    analysis = get_analyses_collection().find_one({"_id": ObjectId(analysis_id)})
    assert analysis["status"] == "completed" and analysis["results"]["graph"] == graph["graph"]
    job = get_jobs_collection().find_one({"job_type": "provenance"})
    assert job["status"] == "completed" and job["output_data"]["relationships_created"] == 1
