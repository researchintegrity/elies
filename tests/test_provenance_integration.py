"""
Test Provenance Analysis Integration
"""
import pytest
from unittest.mock import patch, MagicMock
from app.services.provenance_service import run_provenance_analysis
from app.tasks.provenance import provenance_analysis_task

# Needs a running MongoDB (and for some tests Redis/CBIR): run with -m integration
pytestmark = pytest.mark.integration

@pytest.fixture
def mock_db_collection():
    with patch("app.services.provenance_service.get_images_collection") as mock_get_images:
        mock_col = MagicMock()
        mock_get_images.return_value = mock_col
        yield mock_col

@pytest.fixture
def mock_analyses_collection():
    with patch("app.tasks.provenance.get_analyses_collection") as mock_get_analyses:
        mock_col = MagicMock()
        mock_get_analyses.return_value = mock_col
        yield mock_col

@pytest.fixture
def mock_analyze_provenance():
    with patch("app.services.provenance_service.analyze_provenance") as mock_analyze:
        yield mock_analyze

def test_run_provenance_analysis_success(mock_db_collection, mock_analyze_provenance):
    # Setup
    user_id = "user123"
    query_image_id = "507f1f77bcf86cd799439011"
    
    # Mock query image
    mock_db_collection.find_one.return_value = {
        "_id": "507f1f77bcf86cd799439011",
        "file_path": "/path/to/query.jpg",
        "filename": "query.jpg",
        "user_id": user_id
    }
    
    # Mock user images list
    mock_db_collection.find.return_value = [
        {
            "_id": "507f1f77bcf86cd799439011",
            "file_path": "/path/to/query.jpg",
            "filename": "query.jpg",
            "image_type": "query"
        },
        {
            "_id": "507f1f77bcf86cd799439012",
            "file_path": "/path/to/other.jpg",
            "filename": "other.jpg",
            "image_type": "dataset"
        }
    ]
    
    # Mock analysis result
    mock_analyze_provenance.return_value = (True, "Success", {"graph": "data"})
    
    # Execute
    success, message, result = run_provenance_analysis(user_id, query_image_id)
    
    # Verify
    assert success is True
    assert result == {"graph": "data"}
    
    # Check if analyze_provenance was called with correct arguments
    mock_analyze_provenance.assert_called_once()
    call_args = mock_analyze_provenance.call_args[1]
    assert call_args["user_id"] == user_id
    assert call_args["query_image"]["path"] == "/path/to/query.jpg"
    assert len(call_args["images"]) == 2

def test_provenance_task_execution(mock_analyses_collection, mock_analyze_provenance):
    # Setup
    analysis_id = "507f1f77bcf86cd799439011"
    user_id = "user123"
    query_image_id = "507f1f77bcf86cd799439011"
    
    # Mock service call inside task
    with patch("app.tasks.provenance.run_provenance_analysis") as mock_run:
        mock_run.return_value = (True, "Success", {"graph": "data"})
        
        # Execute task
        result = provenance_analysis_task(
            analysis_id=analysis_id,
            user_id=user_id,
            query_image_id=query_image_id
        )
        
        # Verify
        assert result["status"] == "completed"
        assert result["result"]["graph"] == "data"
        
        # Verify DB updates
        assert mock_analyses_collection.update_one.call_count == 2
        # First update: status processing
        # Second update: status completed
