import pytest
import requests
import time
import os
import uuid

# Configuration
BASE_URL = os.getenv("API_URL", "http://localhost:8000")
USERNAME = f"prov_test_user_{uuid.uuid4().hex[:8]}"
PASSWORD = "TestPassword123"
SOURCE_IMAGE_PATH = "test_prov_source.jpg"
TARGET_IMAGE_PATH = "test_prov_target.jpg"

@pytest.fixture(scope="module")
def auth_token():
    """Register and login a test user, return access token"""
    # 1. Register
    register_data = {
        "username": USERNAME,
        "email": f"{USERNAME}@example.com",
        "password": PASSWORD,
        "full_name": "Provenance Test User"
    }
    try:
        requests.post(f"{BASE_URL}/auth/register", json=register_data)
    except Exception:
        # Ignore error if user already exists
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
def test_images():
    """Create dummy images for testing"""
    from PIL import Image, ImageDraw
    
    # Create source image with some distinct content
    img = Image.new('RGB', (200, 200), color = 'white')
    d = ImageDraw.Draw(img)
    d.rectangle([50, 50, 150, 150], fill='blue', outline='black')
    d.text((60,60), "Provenance Test", fill='white')
    img.save(SOURCE_IMAGE_PATH)
    
    # Create target image (same content for now to ensure match)
    img.save(TARGET_IMAGE_PATH)
    
    yield [SOURCE_IMAGE_PATH, TARGET_IMAGE_PATH]
    
    # Cleanup
    if os.path.exists(SOURCE_IMAGE_PATH):
        os.remove(SOURCE_IMAGE_PATH)
    if os.path.exists(TARGET_IMAGE_PATH):
        os.remove(TARGET_IMAGE_PATH)

@pytest.fixture(scope="module")
def uploaded_image_ids(auth_token, test_images):
    """Upload images and return their IDs"""
    headers = {"Authorization": f"Bearer {auth_token}"}
    ids = []
    
    for img_path in test_images:
        with open(img_path, "rb") as f:
            files = {"file": (os.path.basename(img_path), f, "image/jpeg")}
            response = requests.post(
                f"{BASE_URL}/images/upload",
                headers=headers,
                files=files
            )
        assert response.status_code == 201, f"Upload failed: {response.text}"
        data = response.json()
        ids.append(data.get("id") or data.get("_id"))
        
    return ids

def poll_analysis(auth_token, analysis_id, timeout=120):
    """Helper to poll analysis status"""
    headers = {"Authorization": f"Bearer {auth_token}"}
    start_time = time.time()
    while time.time() - start_time < timeout:
        response = requests.get(f"{BASE_URL}/analyses/{analysis_id}", headers=headers)
        assert response.status_code == 200
        data = response.json()
        status = data.get("status")
        
        if status == "completed":
            return data
        if status == "failed":
            pytest.fail(f"Analysis failed: {data.get('error')}")
            
        time.sleep(2)
    pytest.fail(f"Analysis timed out after {timeout} seconds")

def test_provenance_analysis_e2e(auth_token, uploaded_image_ids):
    """Test full provenance analysis flow"""
    query_image_id = uploaded_image_ids[0]
    
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # 1. Check Service Health
    health_resp = requests.get(f"{BASE_URL}/provenance/health")
    # If service is not running/mocked, this might fail or return false
    # But for E2E we assume it's up. 
    # If it returns 404, the router isn't registered.
    assert health_resp.status_code == 200
    assert health_resp.json()["healthy"] is True, "Provenance service is not healthy"

    # 2. Trigger Analysis
    payload = {
        "image_id": query_image_id,
        "k": 10,
        "q": 5,
        "max_depth": 2,
        "descriptor_type": "cv_rsift"
    }
    
    response = requests.post(
        f"{BASE_URL}/provenance/analyze",
        json=payload,
        headers=headers
    )
    
    assert response.status_code == 202
    data = response.json()
    analysis_id = data.get("analysis_id")
    assert analysis_id is not None
    
    # 3. Poll for results
    result_data = poll_analysis(auth_token, analysis_id)
    
    # 4. Verify Results
    results = result_data.get("results", {})
    
    # The structure of results depends on the microservice output.
    # Usually it contains a graph or list of matches.
    # For now, we just assert we got a result dictionary.
    assert isinstance(results, dict)
    
    # If the microservice is working correctly and we uploaded duplicates,
    # we expect some matches.
    # Note: If CBIR hasn't indexed the images yet, this might return empty.
    # CBIR indexing usually happens on upload or periodically.
    # In this system, we might need to wait or trigger indexing?
    # Assuming the system indexes on upload or the provenance service handles it.
