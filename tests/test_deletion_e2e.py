
import pytest
import requests
import os
import uuid
from pathlib import Path
from app.config.settings import convert_container_path_to_host

# Configuration
BASE_URL = os.getenv("API_URL", "http://localhost:8000")
# Use a unique username to avoid conflicts if cleanup fails
UNIQUE_SUFFIX = str(uuid.uuid4())[:8]
USERNAME = f"test_deletion_user_{UNIQUE_SUFFIX}"
PASSWORD = "TestPassword123"
TEST_IMAGE_PATH = "test_deletion_image.jpg"
TEST_PDF_PATH = "test_deletion_doc.pdf"

@pytest.fixture(scope="module")
def auth_token():
    """Register and login a test user, return access token"""
    # 1. Register
    register_data = {
        "username": USERNAME,
        "email": f"{USERNAME}@example.com",
        "password": PASSWORD,
        "full_name": "Deletion Test User"
    }
    try:
        requests.post(f"{BASE_URL}/auth/register", json=register_data)
    except Exception:
        # If registration fails (e.g. user already exists), just pass
        pass

    # 2. Login
    login_data = {
        "username": USERNAME,
        "password": PASSWORD
    }
    response = requests.post(f"{BASE_URL}/auth/login", data=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["access_token"]

@pytest.fixture(scope="module")
def test_image_file():
    """Create a dummy image file for testing"""
    if not os.path.exists(TEST_IMAGE_PATH):
        from PIL import Image, ImageDraw
        img = Image.new('RGB', (100, 100), color = 'blue')
        d = ImageDraw.Draw(img)
        d.text((10,10), "Delete Me", fill=(255,255,255))
        img.save(TEST_IMAGE_PATH)
    
    yield TEST_IMAGE_PATH
    
    # Cleanup
    if os.path.exists(TEST_IMAGE_PATH):
        os.remove(TEST_IMAGE_PATH)

@pytest.fixture(scope="module")
def test_pdf_file():
    """Create a dummy PDF file for testing"""
    if not os.path.exists(TEST_PDF_PATH):
        # Create a minimal valid PDF file
        pdf_content = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<<\n/Type /Catalog\n/Pages 2 0 R\n>>\nendobj\n"
            b"2 0 obj\n<<\n/Type /Pages\n/Kids [3 0 R]\n/Count 1\n>>\nendobj\n"
            b"3 0 obj\n<<\n/Type /Page\n/Parent 2 0 R\n/MediaBox [0 0 612 792]\n/Resources <<\n>>\n/Contents 4 0 R\n>>\nendobj\n"
            b"4 0 obj\n<<\n/Length 21\n>>\nstream\nHello World\nendstream\nendobj\n"
            b"xref\n0 5\n0000000000 65535 f\n0000000010 00000 n\n0000000060 00000 n\n0000000157 00000 n\n0000000266 00000 n\n"
            b"trailer\n<<\n/Size 5\n/Root 1 0 R\n>>\nstartxref\n316\n%%EOF"
        )
        with open(TEST_PDF_PATH, "wb") as f:
            f.write(pdf_content)
    
    yield TEST_PDF_PATH
    
    # Cleanup
    if os.path.exists(TEST_PDF_PATH):
        os.remove(TEST_PDF_PATH)

def test_upload_and_delete_image(auth_token, test_image_file):
    """
    Test uploading an image and then deleting it.
    Verifies:
    1. Image is uploaded successfully.
    2. Image file exists on disk (host filesystem).
    3. Image is deleted successfully via API.
    4. Image is removed from DB (API returns 404).
    5. Image file is removed from disk.
    """
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # 1. Upload Image
    with open(test_image_file, "rb") as f:
        files = {"file": (os.path.basename(test_image_file), f, "image/jpeg")}
        response = requests.post(
            f"{BASE_URL}/images/upload",
            headers=headers,
            files=files
        )
    
    assert response.status_code == 201, f"Upload failed: {response.text}"
    data = response.json()
    image_id = data.get("id") or data.get("_id")
    file_path = data.get("file_path")
    
    # 2. Verify file exists on disk
    # and we are running tests on the host.
    file_path = str(convert_container_path_to_host(Path(file_path)))
    
    assert os.path.exists(file_path), f"File should exist at {file_path}"
    
    # 3. Delete Image
    del_response = requests.delete(
        f"{BASE_URL}/images/{image_id}",
        headers=headers
    )
    assert del_response.status_code in [200, 204], f"Deletion failed: {del_response.text}"
    
    # 4. Verify Image removed from DB
    get_response = requests.get(
        f"{BASE_URL}/images/{image_id}",
        headers=headers
    )
    assert get_response.status_code == 404, "Image should be not found in DB"
    
    # 5. Verify Image file is removed from disk
    assert not os.path.exists(file_path), f"File should be removed from {file_path}"
    assert get_response.status_code == 404, "Image should be not found in DB"
    
    # 5. Verify file removed from disk
    assert not os.path.exists(file_path), f"File should be deleted from {file_path}"


def test_upload_and_delete_pdf(auth_token, test_pdf_file):
    """
    Test uploading a PDF and then deleting it.
    Verifies:
    1. PDF is uploaded successfully.
    2. PDF file exists on disk.
    3. PDF is deleted successfully via API.
    4. PDF is removed from DB.
    5. PDF file is removed from disk.
    """
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # 1. Upload PDF
    with open(test_pdf_file, "rb") as f:
        files = {"file": (os.path.basename(test_pdf_file), f, "application/pdf")}
        response = requests.post(
            f"{BASE_URL}/documents/upload",
            headers=headers,
            files=files
        )
    
    assert response.status_code == 201, f"Upload failed: {response.text}"
    data = response.json()
    doc_id = data.get("id") or data.get("_id")
    file_path = data.get("file_path")
    
    
    # 2. Verify file exists on disk
    file_path = str(convert_container_path_to_host(Path(file_path)))
    
    assert os.path.exists(file_path), f"File should exist at {file_path}"
    
    # 3. Delete PDF
    del_response = requests.delete(
        f"{BASE_URL}/documents/{doc_id}",
        headers=headers
    )
    assert del_response.status_code in [200, 204], f"Deletion failed: {del_response.text}"
    
    # 4. Verify PDF removed from DB
    get_response = requests.get(
        f"{BASE_URL}/documents/{doc_id}",
        headers=headers
    )
    assert get_response.status_code == 404, "Document should be not found in DB"
    
    # 5. Verify PDF file is removed from disk
    assert not os.path.exists(file_path), f"File should be removed from {file_path}"
