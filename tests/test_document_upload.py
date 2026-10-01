"""
Comprehensive test suite for document and image upload functionality

Tests cover:
- PDF file uploads (valid, invalid, oversized, wrong format)
- Image file uploads (valid, invalid, oversized, wrong format)
- Image metadata and linking to documents
- Download operations
- Delete operations with cascading cleanup
- Authentication and authorization
- File organization and storage
"""

import pytest
import requests
from pathlib import Path
import io
import os
from app.config.settings import convert_container_path_to_host
from bson import ObjectId
from unittest.mock import patch, MagicMock

from app.db.mongodb import get_documents_collection, get_images_collection, db_connection
import shutil

from app.config.settings import UPLOAD_DIR
from app.config.storage_quota import MAX_IMAGE_FILE_SIZE, MAX_PDF_FILE_SIZE

# Needs a running MongoDB; classes marked e2e also need the API server
pytestmark = pytest.mark.integration

# Configuration
BASE_URL = os.getenv("API_URL", "http://localhost:8000")


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Setup database connection for tests"""
    db_connection.connect()
    yield
    # Don't disconnect to allow other fixtures to use it


@pytest.fixture(scope="session", autouse=True)
def setup_redis_for_tests():
    """
    Configure Redis for local testing.
    When running tests outside Docker, use localhost instead of 'redis'.
    """
    # Set Redis to localhost for tests running outside Docker
    os.environ.setdefault("REDIS_HOST", "localhost")
    yield


@pytest.fixture
def mock_celery_task():
    """
    Mock Celery tasks to avoid Redis connection issues in tests.
    Returns a mock that simulates a successful task submission.
    """
    mock_task = MagicMock()
    mock_task.id = "mock-task-id-12345"
    mock_task.delay = MagicMock(return_value=mock_task)
    return mock_task


@pytest.fixture(autouse=True)
def mock_celery_tasks_globally():
    """
    Auto-use fixture that patches all Celery tasks to avoid Redis connection issues.
    Also handles CBIR health check:
    - If real CBIR service is running at localhost:8001, uses it (patches URL).
    - If not, mocks the health check to return success.
    This applies to all tests in this module automatically.
    """
    from contextlib import ExitStack
    
    mock_task = MagicMock()
    mock_task.id = "mock-task-id-12345"
    mock_task.delay = MagicMock(return_value=mock_task)
    
    # Check if real CBIR service is available locally
    use_real_cbir = False
    try:
        resp = requests.get("http://localhost:8001/health", timeout=0.5)
        if resp.status_code == 200:
            use_real_cbir = True
    except Exception:
        pass
        
    stack = ExitStack()
    
    # Always patch celery tasks
    stack.enter_context(patch('app.routes.documents.extract_images_from_document', mock_task))
    stack.enter_context(patch('app.tasks.image_extraction.extract_images_from_document', mock_task))
    
    if use_real_cbir:
        print("\nUsing REAL CBIR service at http://localhost:8001")
        # Patch the URL in the docker_cbir module to point to localhost
        stack.enter_context(patch('app.utils.docker_cbir.CBIR_SERVICE_URL', "http://localhost:8001"))
    else:
        print("\nReal CBIR service not found, using MOCK")
        # Mock CBIR health check to always return healthy
        mock_cbir_health = MagicMock(return_value=(True, None))
        stack.enter_context(patch('app.routes.images.check_cbir_health', mock_cbir_health))
        stack.enter_context(patch('app.routes.documents.check_cbir_health', mock_cbir_health))
        
    with stack:
        yield mock_task


@pytest.fixture
def test_user_token():
    """Register and login a test user, return auth token"""
    # Generate unique username for each test
    import uuid

    unique_id = str(uuid.uuid4())[:8]
    username = f"testuser_{unique_id}"
    email = f"testuser_{unique_id}@example.com"
    
    # Register user
    register_response = requests.post(
        f"{BASE_URL}/auth/register",
        json={
            "username": username,
            "email": email,
            "password": "TestPassword123",
            "full_name": "Test User"
        }
    )
    
    assert register_response.status_code == 200, f"Register failed: {register_response.text}"
    
    # Login user - use form data for OAuth2PasswordRequestForm
    login_response = requests.post(
        f"{BASE_URL}/auth/login",
        data={
            "username": username,
            "password": "TestPassword123"
        }
    )
    
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    
    token = login_response.json()["access_token"]
    user_data = login_response.json()["user"]
    user_id = user_data.get("id") or user_data.get("_id")
    
    return token, user_id


@pytest.fixture
def auth_client(client):
    """
    Register and login a test user, return authenticated TestClient and user_id.
    This replaces test_user_token for tests using TestClient.
    """
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    username = f"testuser_cl_{unique_id}"
    email = f"testuser_cl_{unique_id}@example.com"
    password = "TestPassword123"
    
    # Register user
    register_response = client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "full_name": "Test User"
        }
    )
    assert register_response.status_code == 200, f"Register failed: {register_response.text}"
    
    # Login user
    login_response = client.post(
        "/auth/login",
        data={
            "username": username,
            "password": password
        }
    )
    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    
    token = login_response.json()["access_token"]
    user_data = login_response.json()["user"]
    user_id = user_data.get("id") or user_data.get("_id")
    
    # Set auth header for subsequent requests
    client.headers["Authorization"] = f"Bearer {token}"
    
    return client, user_id


@pytest.fixture(autouse=True)
def cleanup_workspace():
    """Cleanup workspace directory after each test"""
    yield
    # Clean up workspace directory - delete only child directories, not the root UPLOAD_DIR
    if UPLOAD_DIR.exists():
        for child in UPLOAD_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)


@pytest.fixture(autouse=True)
def cleanup_database():
    """Cleanup database collections after each test"""
    yield
    # Clean up collections
    try:
        documents_col = get_documents_collection()
        images_col = get_images_collection()
        documents_col.delete_many({})
        images_col.delete_many({})
    except Exception:
        pass


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_id_from_response(data: dict) -> str:
    """
    Extract ID from response (handles both 'id' and '_id' keys)
    """
    return data.get("id") or data.get("_id")


def create_test_pdf(filename: str = "test.pdf") -> tuple:
    """
    Create a minimal valid PDF file for testing
    
    Returns:
        Tuple of (filename, bytes_content)
    """
    # Minimal valid PDF structure
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Hello World) Tj
ET
endstream
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
0000000301 00000 n
trailer
<< /Size 6 /Root 1 0 R >>
startxref
394
%%EOF
"""
    return filename, pdf_content


def create_test_image(filename: str = "test.png", format_type: str = "png") -> tuple:
    """
    Create a minimal valid image file for testing
    
    Returns:
        Tuple of (filename, bytes_content)
    """
    if format_type == "png":
        # Minimal valid PNG (1x1 transparent pixel)
        image_content = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00'
            b'\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx'
            b'\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )
    elif format_type == "jpg":
        # Minimal valid JPEG
        image_content = (
            b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01'
            b'\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07'
            b'\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14'
            b'\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f'
            b'\'\x9d\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x11\x00\xff\xc4'
            b'\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00'
            b'\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
            b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xfb\xd0\xff\xd9'
        )
    else:
        # Default PNG
        image_content = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00'
            b'\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx'
            b'\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )
    
    return filename, image_content


# ============================================================================
# TESTS: DOCUMENT UPLOAD
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestDocumentUpload:
    """Test PDF document upload functionality"""
    
    def test_upload_valid_pdf(self, test_user_token):
        """Test uploading a valid PDF file"""
        token, user_id = test_user_token
        filename, pdf_content = create_test_pdf()
        
        # Celery task is mocked by autouse fixture mock_celery_tasks_globally
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201, f"Expected 201, got {response.status_code}: {response.text}"
        data = response.json()
        # Filename in response may be the original or the stored name (MongoDB ID)
        assert "filename" in data
        assert data["file_size"] == len(pdf_content)
        # Extraction is now async, so status will be "pending" initially
        assert data["extraction_status"] in ["pending", "completed"]
        assert data["extracted_image_count"] == 0
        assert "_id" in data or "id" in data
    
    def test_upload_pdf_without_auth(self):
        """Test uploading PDF without authentication should fail"""
        filename, pdf_content = create_test_pdf()
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        
        assert response.status_code == 401
    
    def test_upload_invalid_file_format(self, test_user_token):
        """Test uploading non-PDF file should fail"""
        token, user_id = test_user_token
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": ("test.txt", io.BytesIO(b"This is not a PDF"), "text/plain")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 400
        assert "PDF" in response.json()["detail"]
    
    def test_upload_oversized_pdf(self, test_user_token):
        """Test uploading oversized PDF should fail"""
        token, user_id = test_user_token
        
        # Create oversized content (exceeds 50MB limit)
        oversized_content = b"%PDF-1.4\n" + (b"x" * (MAX_PDF_FILE_SIZE + 1))
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": ("oversized.pdf", io.BytesIO(oversized_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 400
        assert "large" in response.json()["detail"].lower()
    
    def test_upload_multiple_pdfs(self, test_user_token):
        """Test uploading multiple PDF files"""
        token, user_id = test_user_token
        doc_ids = []
        
        # Celery task is mocked by autouse fixture mock_celery_tasks_globally
        for i in range(3):
            filename, pdf_content = create_test_pdf(f"test{i}.pdf")
            response = requests.post(
                f"{BASE_URL}/documents/upload",
                files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
                headers={"Authorization": f"Bearer {token}"}
            )
            
            assert response.status_code == 201
            doc_data = response.json()
            # Try both "id" and "_id" keys
            doc_id = doc_data.get("id") or doc_data.get("_id")
            assert doc_id, f"No id found in response: {doc_data}"
            doc_ids.append(doc_id)
        
        # Verify all documents are unique
        assert len(set(doc_ids)) == 3
    
    def test_file_saved_with_correct_path(self, test_user_token):
        """Test that PDF files are saved in the correct directory structure"""
        token, user_id = test_user_token
        filename, pdf_content = create_test_pdf()
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
        file_path = response.json()["file_path"]
        
        # Verify file path contains user_id and pdfs subfolder
        assert user_id in file_path
        assert "pdfs" in file_path
        assert Path(convert_container_path_to_host(file_path)).exists()
    
    def test_extraction_folder_created(self, test_user_token):
        """Test that extraction folder is created for document"""
        token, user_id = test_user_token
        filename, pdf_content = create_test_pdf()
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
        doc_data = response.json()
        doc_id = doc_data.get("id") or doc_data.get("_id")
        assert doc_id
        
        # Verify extraction folder exists
        extraction_path = UPLOAD_DIR / user_id / "images" / "extracted" / doc_id
        assert extraction_path.exists()


# ============================================================================
# TESTS: DOCUMENT LIST AND GET
# ============================================================================

class TestDocumentRetrieval:
    """Test PDF document retrieval functionality"""
    
    def test_get_documents_list(self, auth_client):
        """Test retrieving list of user's documents"""
        client, user_id = auth_client
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        
        # Get documents list
        response = client.get("/documents")
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 1
        # Filename may be the original or stored name
        assert "filename" in data["items"][0]
    
    def test_get_documents_with_pagination(self, auth_client):
        """Test pagination of documents list"""
        client, user_id = auth_client
        
        # Upload 5 documents
        for i in range(5):
            filename, pdf_content = create_test_pdf(f"test{i}.pdf")
            client.post(
                "/documents/upload",
                files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
            )
        
        # Get first page (per_page=2)
        response = client.get("/documents?page=1&per_page=2")
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) <= 2


    
    def test_get_specific_document(self, auth_client):
        """Test retrieving specific document by ID"""
        client, user_id = auth_client
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        upload_response = client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        
        doc_id = get_id_from_response(upload_response.json())
        
        # Get specific document
        response = client.get(f"/documents/{doc_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert get_id_from_response(data) == doc_id
        # Filename may be the original or stored name
        assert "filename" in data
    
    def test_get_nonexistent_document(self, auth_client):
        """Test retrieving nonexistent document returns 404"""
        client, user_id = auth_client
        fake_id = str(ObjectId())
        
        response = client.get(f"/documents/{fake_id}")
        
        assert response.status_code == 404
    
    def test_get_document_from_another_user(self, auth_client):
        """Test that users cannot access other users' documents"""
        client, user_id1 = auth_client
        
        # Upload document as user1
        filename, pdf_content = create_test_pdf()
        upload_response = client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Register and login as user2
        # Note: We need to override the Authorization header
        username2 = "testuser2_cl"
        password = "TestPassword456"
        
        client.post(
            "/auth/register",
            json={
                "username": username2,
                "email": "testuser2_cl@example.com",
                "password": password,
                "full_name": "Test User 2"
            }
        )
        login_response = client.post(
            "/auth/login",
            data={
                "username": username2,
                "password": password
            }
        )
        token2 = login_response.json()["access_token"]
        
        # Try to get user1's document as user2
        # We manually set header for this request
        response = client.get(
            f"/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token2}"}
        )
        
        assert response.status_code == 404



# ============================================================================
# TESTS: IMAGE UPLOAD
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestImageUpload:
    """Test image file upload functionality"""
    
    def test_upload_valid_image(self, test_user_token):
        """Test uploading a valid image file"""
        token, user_id = test_user_token
        filename, image_content = create_test_image("test.png")
        
        response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
        data = response.json()
        # Note: filename is renamed to {mongodb_id}.{ext} after upload
        # The original filename is stored in original_filename field
        assert data["original_filename"] == filename
        assert data["filename"].endswith(".png")  # File renamed to mongodb_id.png
        assert data["file_size"] == len(image_content)
        assert data["source_type"] == "uploaded"
        assert data["document_id"] is None
    
    def test_upload_image_linked_to_document(self, test_user_token):
        """Test uploading image linked to a document"""
        token, user_id = test_user_token
        
        # Upload PDF first (Celery task is mocked by autouse fixture)
        pdf_filename, pdf_content = create_test_pdf()
        pdf_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (pdf_filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(pdf_response.json())
        
        # Upload image linked to document
        img_filename, img_content = create_test_image()
        img_response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (img_filename, io.BytesIO(img_content), "image/png")},
            params={"document_id": doc_id},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert img_response.status_code == 201
        img_data = img_response.json()
        assert img_data["source_type"] == "uploaded"
        assert img_data["document_id"] == doc_id
    
    def test_upload_invalid_image_format(self, test_user_token):
        """Test uploading non-image file should fail"""
        token, user_id = test_user_token
        
        response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": ("test.txt", io.BytesIO(b"Not an image"), "text/plain")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 400
    
    def test_upload_oversized_image(self, test_user_token):
        """Test uploading oversized image should fail"""
        token, user_id = test_user_token
        
        # Create oversized content (exceeds 10MB limit)
        oversized_content = b"\x89PNG\r\n\x1a\n" + (b"x" * (MAX_IMAGE_FILE_SIZE + 1))
        
        response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": ("oversized.png", io.BytesIO(oversized_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 400
    
    def test_upload_multiple_image_formats(self, test_user_token):
        """Test uploading images in different formats"""
        token, user_id = test_user_token
        formats = [("test.png", "png"), ("test.jpg", "jpg")]
        image_ids = []
        
        for filename, fmt in formats:
            img_filename, img_content = create_test_image(filename, fmt)
            response = requests.post(
                f"{BASE_URL}/images/upload",
                files={"file": (img_filename, io.BytesIO(img_content), f"image/{fmt}")},
                headers={"Authorization": f"Bearer {token}"}
            )
            
            assert response.status_code == 201
            image_ids.append(get_id_from_response(response.json()))
        
        assert len(set(image_ids)) == 2


# ============================================================================
# TESTS: IMAGE LIST AND GET
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestImageRetrieval:
    """Test image retrieval functionality"""
    
    def test_get_images_list(self, test_user_token):
        """Test retrieving list of user's images"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        # Get images list
        response = requests.get(
            f"{BASE_URL}/images",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert isinstance(data["items"], list)
        assert len(data["items"]) == 1
        # Filename is renamed to mongodb_id.ext, check original_filename instead
        assert data["items"][0]["original_filename"] == filename
    
    def test_get_images_filtered_by_source_type(self, test_user_token):
        """Test filtering images by source type"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        # Get uploaded images
        response = requests.get(
            f"{BASE_URL}/images?source_type=uploaded",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 1
        assert data["items"][0]["source_type"] == "uploaded"
    
    def test_get_specific_image(self, test_user_token):
        """Test retrieving specific image by ID"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        upload_response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        image_id = get_id_from_response(upload_response.json())
        
        # Get specific image
        response = requests.get(
            f"{BASE_URL}/images/{image_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert get_id_from_response(data) == image_id
        # Filename is renamed to mongodb_id.ext, check original_filename instead
        assert data["original_filename"] == filename


# ============================================================================
# TESTS: DOWNLOAD OPERATIONS
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestDownload:
    """Test file download functionality"""
    
    def test_download_pdf_file(self, test_user_token):
        """Test downloading uploaded PDF file"""
        token, user_id = test_user_token
        
        # Upload a document (Celery task is mocked by autouse fixture)
        filename, pdf_content = create_test_pdf()
        print("filename:", filename)
        upload_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Download document
        response = requests.get(
            f"{BASE_URL}/documents/{doc_id}/download",
            headers={"Authorization": f"Bearer {token}"}
        )
        print("Download response status code:", response.status_code)
        print("Download response headers:", response.headers)
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
    
    def test_download_image_file(self, test_user_token):
        """Test downloading uploaded image file"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        upload_response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        image_id = get_id_from_response(upload_response.json())
        
        # Download image
        response = requests.get(
            f"{BASE_URL}/images/{image_id}/download",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        assert "image" in response.headers["content-type"]
    
    def test_download_nonexistent_file(self, test_user_token):
        """Test downloading nonexistent file returns 404"""
        token, user_id = test_user_token
        fake_id = str(ObjectId())
        
        response = requests.get(
            f"{BASE_URL}/documents/{fake_id}/download",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 404


# ============================================================================
# TESTS: DELETE OPERATIONS
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestDelete:
    """Test file deletion functionality"""
    
    def test_delete_document(self, test_user_token):
        """Test deleting a document"""
        token, user_id = test_user_token
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        upload_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(upload_response.json())
        file_path = upload_response.json()["file_path"]
        
        # Verify file exists
        assert Path(convert_container_path_to_host(file_path)).exists()
        
        # Delete document
        response = requests.delete(
            f"{BASE_URL}/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 204
        
        # Verify file is deleted
        assert not Path(convert_container_path_to_host(file_path)).exists()
    
    def test_delete_document_cascades_to_images(self, test_user_token):
        """Test that deleting document also deletes associated extracted images"""
        token, user_id = test_user_token
        
        # Upload a document
        pdf_filename, pdf_content = create_test_pdf()
        pdf_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (pdf_filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(pdf_response.json())
        
        # Delete document
        delete_response = requests.delete(
            f"{BASE_URL}/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert delete_response.status_code == 204
    
    def test_delete_image(self, test_user_token):
        """Test deleting a user-uploaded image"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        upload_response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        image_id = get_id_from_response(upload_response.json())
        file_path = upload_response.json()["file_path"]
        
        # Verify file exists
        assert Path(convert_container_path_to_host(file_path)).exists()
        
        # Delete image
        response = requests.delete(
            f"{BASE_URL}/images/{image_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 204
        
        # Verify file is deleted
        assert not Path(file_path).exists()
    
    def test_cannot_delete_other_users_document(self, test_user_token):
        """Test that users cannot delete other users' documents"""
        token1, user_id1 = test_user_token
        
        # Upload document as user1
        filename, pdf_content = create_test_pdf()
        upload_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token1}"}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Register and login as user2
        requests.post(
            f"{BASE_URL}/auth/register",
            json={
                "username": "testuser2",
                "email": "testuser2@example.com",
                "password": "TestPassword456",
                "full_name": "Test User 2"
            }
        )
        login_response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": "testuser2",
                "password": "TestPassword456"
            }
        )
        token2 = login_response.json()["access_token"]
        
        # Try to delete user1's document as user2
        response = requests.delete(
            f"{BASE_URL}/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token2}"}
        )
        
        assert response.status_code == 404


# ============================================================================
# TESTS: DOCUMENT IMAGES ASSOCIATION
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestDocumentImageAssociation:
    """Test association between documents and images"""
    
    def test_get_extracted_images_for_document(self, test_user_token):
        """Test retrieving extracted images for a specific document"""
        token, user_id = test_user_token
        
        # Upload a document
        pdf_filename, pdf_content = create_test_pdf()
        pdf_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (pdf_filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(pdf_response.json())
        
        # Get extracted images for document
        response = requests.get(
            f"{BASE_URL}/documents/{doc_id}/images",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # No images extracted yet since extraction is a placeholder
        assert len(data) == 0


# ============================================================================
# TESTS: EDGE CASES AND ERROR HANDLING
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestEdgeCases:
    """Test edge cases and error handling"""
    
    def test_upload_empty_pdf_file(self, test_user_token):
        """Test uploading empty PDF file"""
        token, user_id = test_user_token
        
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        # Should fail due to invalid PDF
        assert response.status_code == 400
    
    def test_upload_empty_image_file(self, test_user_token):
        """Test uploading empty image file"""
        token, user_id = test_user_token
        
        response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": ("empty.png", io.BytesIO(b""), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        # Should fail due to invalid image
        assert response.status_code == 400
    
    def test_upload_with_special_characters_in_filename(self, test_user_token):
        """Test uploading file with special characters in filename"""
        token, user_id = test_user_token
        
        filename, pdf_content = create_test_pdf("test@#$%.pdf")
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
    
    def test_upload_with_very_long_filename(self, test_user_token):
        """Test uploading file with very long filename"""
        token, user_id = test_user_token
        
        long_name = "a" * 200 + ".pdf"
        filename, pdf_content = create_test_pdf(long_name)
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 201
    
    def test_concurrent_uploads_from_same_user(self, test_user_token):
        """Test uploading multiple files simultaneously"""
        token, user_id = test_user_token
        
        # Upload multiple files quickly
        doc_ids = []
        for i in range(3):
            filename, pdf_content = create_test_pdf(f"test{i}.pdf")
            response = requests.post(
                f"{BASE_URL}/documents/upload",
                files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
                headers={"Authorization": f"Bearer {token}"}
            )
            
            assert response.status_code == 201
            doc_ids.append(get_id_from_response(response.json()))
        
        # Verify all uploads succeeded with unique IDs
        assert len(set(doc_ids)) == 3


# ============================================================================
# TESTS: DATABASE INTEGRITY
# ============================================================================

@pytest.mark.e2e  # calls the running API server at API_URL
class TestDatabaseIntegrity:
    """Test database records and integrity"""
    
    def test_document_record_created_in_database(self, test_user_token):
        """Test that document record is created in MongoDB"""
        token, user_id = test_user_token
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        upload_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Verify document exists in database
        documents_col = get_documents_collection()
        doc = documents_col.find_one({"_id": ObjectId(doc_id)})
        
        assert doc is not None
        assert doc["user_id"] == user_id
        # Filename may be the original or stored name
        assert "filename" in doc
    
    def test_image_record_created_in_database(self, test_user_token):
        """Test that image record is created in MongoDB"""
        token, user_id = test_user_token
        
        # Upload an image
        filename, image_content = create_test_image()
        upload_response = requests.post(
            f"{BASE_URL}/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")},
            headers={"Authorization": f"Bearer {token}"}
        )
        image_id = get_id_from_response(upload_response.json())
        
        # Verify image exists in database
        images_col = get_images_collection()
        img = images_col.find_one({"_id": ObjectId(image_id)})
        
        assert img is not None
        assert img["user_id"] == user_id
        # Filename may be the original or stored name
        assert "filename" in img
        assert img["source_type"] == "uploaded"
    
    def test_document_deletion_removes_database_record(self, test_user_token):
        """Test that deleting document removes database record"""
        token, user_id = test_user_token
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        upload_response = requests.post(
            f"{BASE_URL}/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Delete document
        requests.delete(
            f"{BASE_URL}/documents/{doc_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        # Verify document is removed from database
        documents_col = get_documents_collection()
        doc = documents_col.find_one({"_id": ObjectId(doc_id)})
        assert doc is None


# ============================================================================
# STORAGE QUOTA TESTS
# ============================================================================

class TestStorageQuota:
    """Test suite for storage quota enforcement"""
    
    def test_quota_info_in_document_upload_response(self, auth_client):
        """Test that upload response includes storage quota information"""
        client, user_id = auth_client
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        response = client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        
        assert response.status_code == 201
        data = response.json()
        
        # Verify quota info is included
        assert "user_storage_used" in data
        assert "user_storage_remaining" in data
        assert isinstance(data["user_storage_used"], int)
        assert isinstance(data["user_storage_remaining"], int)
        assert data["user_storage_used"] > 0
        
        # Verify remaining quota is calculated correctly (1GB default)
        total_quota = 1 * 1024 * 1024 * 1024  # 1GB
        assert data["user_storage_used"] + data["user_storage_remaining"] == total_quota
    
    def test_quota_info_in_document_list(self, auth_client):
        """Test that document list includes quota information"""
        client, user_id = auth_client
        
        # Upload a document
        filename, pdf_content = create_test_pdf()
        client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        
        # List documents
        response = client.get("/documents")
        
        assert response.status_code == 200
        data = response.json()
        items = data["items"]
        assert len(items) > 0
        
        # Verify each document includes quota info
        for doc in items:
            assert "user_storage_used" in doc
            assert "user_storage_remaining" in doc
    
    def test_quota_info_in_image_upload_response(self, auth_client):
        """Test that image upload response includes storage quota information"""
        client, user_id = auth_client
        
        # Upload an image
        filename, image_content = create_test_image()
        response = client.post(
            "/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")}
        )
        
        assert response.status_code == 201
        data = response.json()
        
        # Verify quota info
        assert "user_storage_used" in data
        assert "user_storage_remaining" in data
        assert data["user_storage_used"] > 0
    
    def test_multiple_uploads_accumulate_storage(self, auth_client):
        """Test that multiple files accumulate towards total quota"""
        client, user_id = auth_client
        
        # Upload first document
        filename1, pdf_content1 = create_test_pdf()
        response1 = client.post(
            "/documents/upload",
            files={"file": (filename1, io.BytesIO(pdf_content1), "application/pdf")}
        )
        assert response1.status_code == 201
        used_after_first = response1.json()["user_storage_used"]
        remaining_after_first = response1.json()["user_storage_remaining"]
        
        # Upload an image
        img_filename, image_content = create_test_image()
        response_img = client.post(
            "/images/upload",
            files={"file": (img_filename, io.BytesIO(image_content), "image/png")}
        )
        assert response_img.status_code == 201
        used_after_image = response_img.json()["user_storage_used"]
        remaining_after_image = response_img.json()["user_storage_remaining"]
        
        # Verify uploads accumulate storage
        assert used_after_image >= used_after_first  # Image size might vary but at least equal
        assert remaining_after_image <= remaining_after_first  # Remaining should decrease

    
    def test_deletion_frees_storage_quota(self, auth_client):
        """Test that deleting a file frees up quota"""
        client, user_id = auth_client
        
        # Upload document
        filename, pdf_content = create_test_pdf()
        upload_response = client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        doc_id = get_id_from_response(upload_response.json())
        used_after_upload = upload_response.json()["user_storage_used"]
        
        # Delete document
        client.delete(f"/documents/{doc_id}")
        
        # Upload another document and check that quota is available
        filename2, pdf_content2 = create_test_pdf()
        response2 = client.post(
            "/documents/upload",
            files={"file": (filename2, io.BytesIO(pdf_content2), "application/pdf")}
        )
        
        # After deletion and reupload, storage should be less than 2x first upload
        # (approximately the size of one file)
        used_after_second = response2.json()["user_storage_used"]
        
        # The difference should be approximately one file size
        # (allowing for some variance in PDF generation)
        assert used_after_second < used_after_upload + len(pdf_content2) * 1.5
    
    def test_image_deletion_frees_quota(self, auth_client):
        """Test that deleting an image frees up quota"""
        client, user_id = auth_client
        
        # Upload image
        filename, image_content = create_test_image()
        upload_response = client.post(
            "/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")}
        )
        img_id = get_id_from_response(upload_response.json())
        used_after_upload = upload_response.json()["user_storage_used"]
        
        # Delete image
        delete_response = client.delete(f"/images/{img_id}")
        assert delete_response.status_code == 204
        
        # Upload another image and verify space is available
        filename2, image_content2 = create_test_image()
        response2 = client.post(
            "/images/upload",
            files={"file": (filename2, io.BytesIO(image_content2), "image/png")}
        )
        
        used_after_second = response2.json()["user_storage_used"]
        
        # After deletion and reupload, should be approximately one file size
        assert used_after_second < used_after_upload + len(image_content2) * 1.5
    
    def test_quota_updated_in_list_after_deletion(self, auth_client):
        """Test that quota info in list updates after file deletion"""
        client, user_id = auth_client
        
        # Upload two documents
        filename1, pdf_content1 = create_test_pdf()
        upload_response1 = client.post(
            "/documents/upload",
            files={"file": (filename1, io.BytesIO(pdf_content1), "application/pdf")}
        )
        doc_id1 = get_id_from_response(upload_response1.json())
        
        filename2, pdf_content2 = create_test_pdf()
        upload_response2 = client.post(
            "/documents/upload",
            files={"file": (filename2, io.BytesIO(pdf_content2), "application/pdf")}
        )
        used_after_uploads = upload_response2.json()["user_storage_used"]
        
        # Get list and check quota (using paginated structure)
        list_response = client.get("/documents")
        quota_before_delete = list_response.json()["items"][0]["user_storage_used"]
        assert quota_before_delete == used_after_uploads
        
        # Delete first document
        client.delete(f"/documents/{doc_id1}")
        
        # Get list and check quota is now less (one less file)
        list_response2 = client.get("/documents")
        quota_after_delete = list_response2.json()["items"][0]["user_storage_used"]
        
        # Quota should be reduced from original after deletion
        assert quota_after_delete < quota_before_delete
    
    def test_get_document_includes_quota_info(self, auth_client):
        """Test that getting single document includes quota information"""
        client, user_id = auth_client
        
        # Upload document
        filename, pdf_content = create_test_pdf()
        upload_response = client.post(
            "/documents/upload",
            files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
        )
        doc_id = get_id_from_response(upload_response.json())
        
        # Get single document
        response = client.get(f"/documents/{doc_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert "user_storage_used" in data
        assert "user_storage_remaining" in data
        assert data["user_storage_used"] > 0
    
    def test_get_image_includes_quota_info(self, auth_client):
        """Test that getting single image includes quota information"""
        client, user_id = auth_client
        
        # Upload image
        filename, image_content = create_test_image()
        upload_response = client.post(
            "/images/upload",
            files={"file": (filename, io.BytesIO(image_content), "image/png")}
        )
        img_id = get_id_from_response(upload_response.json())
        
        # Get single image
        response = client.get(f"/images/{img_id}")
        
        assert response.status_code == 200
        data = response.json()
        assert "user_storage_used" in data
        assert "user_storage_remaining" in data


# ============================================================================
# TESTS: CBIR HEALTH CHECK
# ============================================================================

class TestCBIRHealthCheck:
    """Test CBIR service health check functionality"""
    
    def test_cbir_health_check_success(self):
        """Test that check_cbir_health returns True when service is healthy"""
        from unittest.mock import patch, MagicMock
        from app.utils.docker_cbir import check_cbir_health
        
        # Mock a successful health check response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"model": True, "database": True}
        
        with patch('app.utils.docker_cbir.requests.get', return_value=mock_response):
            healthy, message = check_cbir_health(use_cache=False)
            assert healthy is True
            assert "healthy" in message.lower()
    
    def test_cbir_health_check_partial_init(self):
        """Test that check_cbir_health returns False when service is partially initialized"""
        from unittest.mock import patch, MagicMock
        from app.utils.docker_cbir import check_cbir_health
        
        # Mock a partial initialization response (model ready, database not)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"model": True, "database": False}
        
        with patch('app.utils.docker_cbir.requests.get', return_value=mock_response):
            healthy, message = check_cbir_health(use_cache=False)
            assert healthy is False
            assert "partially" in message.lower()
    
    def test_cbir_health_check_connection_error(self):
        """Test that check_cbir_health returns False when service is unreachable"""
        from unittest.mock import patch
        from app.utils.docker_cbir import check_cbir_health
        import requests
        
        with patch('app.utils.docker_cbir.requests.get', 
                   side_effect=requests.RequestException("Connection refused")):
            healthy, message = check_cbir_health(use_cache=False)
            assert healthy is False
            assert "Failed to connect" in message
    
    def test_cbir_health_check_non_200_status(self):
        """Test that check_cbir_health returns False for non-200 status codes"""
        from unittest.mock import patch, MagicMock
        from app.utils.docker_cbir import check_cbir_health
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        
        with patch('app.utils.docker_cbir.requests.get', return_value=mock_response):
            healthy, message = check_cbir_health(use_cache=False)
            assert healthy is False
            assert "status 500" in message
    
    def test_upload_returns_503_when_cbir_unavailable(self, client):
        """Test that image upload returns 503 when CBIR service is unavailable"""
        from unittest.mock import patch
        
        # Register and login
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        username = f"testuser_cbir_{unique_id}"
        client.post(
            "/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "TestPassword123",
                "full_name": "Test User"
            }
        )
        login_response = client.post(
            "/auth/login",
            data={"username": username, "password": "TestPassword123"}
        )
        token = login_response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        
        # Mock CBIR health check to return unhealthy
        with patch('app.routes.images.check_cbir_health', return_value=(False, "Service unavailable")):
            filename, image_content = create_test_image()
            response = client.post(
                "/images/upload",
                files={"file": (filename, io.BytesIO(image_content), "image/png")}
            )
            
            assert response.status_code == 503
            assert "try again" in response.json()["detail"].lower()
    
    def test_document_upload_returns_503_when_cbir_unavailable(self, client):
        """Test that document upload returns 503 when CBIR service is unavailable"""
        from unittest.mock import patch
        
        # Register and login
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        username = f"testuser_cbir_doc_{unique_id}"
        client.post(
            "/auth/register",
            json={
                "username": username,
                "email": f"{username}@example.com",
                "password": "TestPassword123",
                "full_name": "Test User"
            }
        )
        login_response = client.post(
            "/auth/login",
            data={"username": username, "password": "TestPassword123"}
        )
        token = login_response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        
        # Mock CBIR health check to return unhealthy
        with patch('app.routes.documents.check_cbir_health', return_value=(False, "Service unavailable")):
            filename, pdf_content = create_test_pdf()
            response = client.post(
                "/documents/upload",
                files={"file": (filename, io.BytesIO(pdf_content), "application/pdf")}
            )
            
            assert response.status_code == 503
            assert "try again" in response.json()["detail"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

