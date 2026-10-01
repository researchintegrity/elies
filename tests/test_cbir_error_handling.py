"""
Unit tests for CBIR error handling and cleanup.

Tests the pre-flight CBIR check and all-or-nothing cleanup behavior.
"""
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import io


class TestPreflightCBIRCheck:
    """Tests for pre-flight CBIR health check in endpoints."""
    
    def test_upload_image_blocked_when_cbir_unavailable(self, client, test_user_token):
        """Test that single upload returns 503 when CBIR is unavailable."""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        with patch('app.routes.images.check_cbir_health') as mock_check:
            mock_check.return_value = (False, "Connection refused")
            
            # Create a simple test image
            
            img = Image.new('RGB', (100, 100), color='red')
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)
            
            files = {"file": ("test.png", img_buffer, "image/png")}
            response = client.post("/images/upload", headers=headers, files=files)
            
            assert response.status_code == 503
            assert "unable to upload" in response.json()["detail"].lower()
    
    def test_batch_upload_blocked_when_cbir_unavailable(self, client, test_user_token):
        """Test that batch upload returns 503 when CBIR is unavailable."""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        with patch('app.routes.images.check_cbir_health') as mock_check:
            mock_check.return_value = (False, "Connection refused")
            
            # Create a simple test image
            from PIL import Image
            import io
            img = Image.new('RGB', (100, 100), color='red')
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)
            
            files = [("files", ("test.png", img_buffer, "image/png"))]
            response = client.post("/images/upload/batch", headers=headers, files=files)
            
            assert response.status_code == 503
            assert "unable to upload" in response.json()["detail"].lower()
    
    def test_panel_extraction_blocked_when_cbir_unavailable(self, client, test_user_token):
        """Test that panel extraction returns 503 when CBIR is unavailable."""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        with patch('app.routes.images.check_cbir_health') as mock_check:
            mock_check.return_value = (False, "Connection refused")
            
            response = client.post(
                "/images/extract-panels",
                headers=headers,
                json={"image_ids": ["507f1f77bcf86cd799439013"]}
            )
            
            assert response.status_code == 503
            assert "unable to upload" in response.json()["detail"].lower()
    
    def test_document_upload_blocked_when_cbir_unavailable(self, client, test_user_token):
        """Test that PDF document upload returns 503 when CBIR is unavailable."""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        with patch('app.routes.documents.check_cbir_health') as mock_check:
            mock_check.return_value = (False, "Connection refused")
            
            # Create a minimal PDF-like file
            import io
            pdf_content = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'
            pdf_buffer = io.BytesIO(pdf_content)
            
            files = {"file": ("test.pdf", pdf_buffer, "application/pdf")}
            response = client.post("/documents/upload", headers=headers, files=files)
            
            assert response.status_code == 503
            assert "unable to upload" in response.json()["detail"].lower()
    
    def test_upload_proceeds_when_cbir_healthy(self, client, test_user_token):
        """Test that upload proceeds when CBIR is healthy."""
        headers = {"Authorization": f"Bearer {test_user_token}"}
        
        with patch('app.routes.images.check_cbir_health') as mock_check:
            mock_check.return_value = (True, "CBIR service is healthy")
            
            # Create a simple test image
            from PIL import Image
            import io
            img = Image.new('RGB', (100, 100), color='red')
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)
            
            files = {"file": ("test.png", img_buffer, "image/png")}
            
            # Patch the CBIR indexing task to avoid actual indexing
            with patch('app.routes.images.cbir_index_image') as mock_index:
                mock_index.delay = MagicMock()
                response = client.post("/images/upload", headers=headers, files=files)
            
            # Should proceed (either 201 success or other error, but not 503)
            assert response.status_code != 503


class TestBatchCleanupOnFailure:
    """Tests for all-or-nothing batch cleanup behavior."""
    
    def test_cbir_index_batch_cleans_up_on_failure(self):
        """Test that cbir_index_batch deletes all images when CBIR fails."""
        from app.tasks.cbir import cbir_index_batch
        
        mock_image_items = [
            {"image_id": "id1", "image_path": "/path/1.png", "labels": []},
            {"image_id": "id2", "image_path": "/path/2.png", "labels": []},
        ]
        
        with patch('app.tasks.cbir.index_images_batch') as mock_index:
            mock_index.return_value = (False, "Connection refused", {})
            
            with patch('app.tasks.cbir._cleanup_batch_images') as mock_cleanup:
                mock_cleanup.return_value = ["id1", "id2"]
                
                result = cbir_index_batch(
                    user_id="test_user",
                    image_items=mock_image_items
                )
                
                # Verify cleanup was called with all image items
                mock_cleanup.assert_called_once()
                call_args = mock_cleanup.call_args
                assert len(call_args[0][0]) == 2  # Two items passed
                
                # Verify result indicates failure and cleanup
                assert result["status"] == "failed"
                assert "deleted_image_ids" in result
                assert len(result["deleted_image_ids"]) == 2
    
    def test_cbir_index_batch_with_progress_cleans_up_on_chunk_failure(self):
        """Test that cbir_index_batch_with_progress deletes all images when a chunk fails."""
        from app.tasks.cbir import cbir_index_batch_with_progress
        
        mock_image_items = [
            {"image_id": "id1", "image_path": "/path/1.png", "labels": []},
            {"image_id": "id2", "image_path": "/path/2.png", "labels": []},
        ]
        
        with patch('app.tasks.cbir.get_indexing_jobs_collection') as mock_jobs_col:
            mock_jobs = MagicMock()
            mock_jobs.find_one.return_value = {"status": "pending"}
            mock_jobs.update_one = MagicMock()
            mock_jobs_col.return_value = mock_jobs
            
            with patch('app.tasks.cbir.get_images_collection') as mock_images_col:
                mock_images_col.return_value = MagicMock()
                
                with patch('app.tasks.cbir.index_images_batch') as mock_index:
                    mock_index.return_value = (False, "CBIR service error", {})
                    
                    with patch('app.tasks.cbir._cleanup_batch_images') as mock_cleanup:
                        mock_cleanup.return_value = ["id1", "id2"]
                        
                        result = cbir_index_batch_with_progress(
                            job_id="test_job_123",
                            user_id="test_user",
                            image_items=mock_image_items
                        )
                        
                        # Verify cleanup was called
                        mock_cleanup.assert_called_once()
                        
                        # Verify result indicates failure
                        assert result["status"] == "failed"
                        assert "deleted_image_ids" in result
                        assert len(result["deleted_image_ids"]) == 2


class TestCleanupHelper:
    """Tests for the _cleanup_batch_images helper function."""
    
    def test_cleanup_batch_images_calls_delete_for_each_image(self):
        """Test that cleanup calls delete_image_and_artifacts for each image."""
        from app.tasks.cbir import _cleanup_batch_images
        
        mock_items = [
            {"image_id": "id1"},
            {"image_id": "id2"},
            {"image_id": "id3"},
        ]
        
        with patch('app.services.image_service.delete_image_and_artifacts') as mock_delete:
            mock_delete.return_value = None
            
            deleted_ids = _cleanup_batch_images(mock_items, "test_user")
            
            # Verify delete was called for each image
            assert mock_delete.call_count == 3
            assert len(deleted_ids) == 3
            assert "id1" in deleted_ids
            assert "id2" in deleted_ids
            assert "id3" in deleted_ids


# Fixtures
@pytest.fixture
def client():
    """Create a test client."""
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def test_user_token(client):
    """Create a test user and return their access token."""
    import uuid
    
    unique_username = f"cbir_error_test_{uuid.uuid4().hex[:8]}"
    
    # Register user
    register_data = {
        "username": unique_username,
        "email": f"{unique_username}@example.com",
        "password": "TestPassword123!",
        "full_name": "CBIR Error Test User"
    }
    
    try:
        client.post("/auth/register", json=register_data)
    except Exception:
        pass
    
    # Login
    login_data = {
        "username": unique_username,
        "password": "TestPassword123!"
    }
    response = client.post("/auth/login", data=login_data)
    
    if response.status_code == 200:
        return response.json()["access_token"]
    
    # If login failed, skip the test
    pytest.skip("Could not create test user")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
