"""
Integration tests for the pre-flight CBIR check on uploads (live MongoDB).

Images are never deleted when indexing fails (#69); that behaviour and the
pre-flight check are covered without services in tests/unit/test_cbir_indexing.py.
"""
import pytest
from unittest.mock import patch, MagicMock
from PIL import Image
import io

# Needs a running MongoDB (and for some tests Redis/CBIR): run with -m integration
pytestmark = pytest.mark.integration


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
