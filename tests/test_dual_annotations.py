"""
Test suite for dual-image annotations
Verifies batch creation, retrieval, and deletion of dual annotations.
"""
import pytest
import requests
import os

from app.db.mongodb import db_connection

# Calls a running API server (API_URL, default http://localhost:8000): run with -m e2e
pytestmark = pytest.mark.e2e

BASE_URL = os.getenv("API_URL", "http://localhost:8000")

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    db_connection.connect()
    yield

@pytest.fixture
def auth_token():
    import uuid
    unique_id = str(uuid.uuid4())[:8]
    test_user_data = {
        "username": f"dual_ann_{unique_id}",
        "email": f"dual_ann_{unique_id}@example.com",
        "password": "Test@Password123",
        "full_name": "Dual Ann User"
    }
    response = requests.post(f"{BASE_URL}/auth/register", json=test_user_data)
    assert response.status_code == 200
    return response.json()["access_token"]

@pytest.fixture
def source_image_id(auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    file_content = b"fakeimagecontent_source"
    files = {"file": ("source.jpg", file_content, "image/jpeg")}
    response = requests.post(f"{BASE_URL}/images/upload", headers=headers, files=files)
    assert response.status_code == 201
    return response.json()["_id"]

@pytest.fixture
def target_image_id(auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    file_content = b"fakeimagecontent_target"
    files = {"file": ("target.jpg", file_content, "image/jpeg")}
    response = requests.post(f"{BASE_URL}/images/upload", headers=headers, files=files)
    assert response.status_code == 201
    return response.json()["_id"]

def test_dual_annotations_batch(auth_token, source_image_id, target_image_id):
    headers = {"Authorization": f"Bearer {auth_token}"}
    link_id = "test_link_dual_123"
    
    # 1. Batch Create
    batch_data = {
        "annotations": [
            {
                "source_image_id": source_image_id,
                "target_image_id": target_image_id,
                "link_id": link_id,
                "text": "Left annotation",
                "coords": {"x": 10, "y": 10, "width": 50, "height": 50},
                "pair_name": "Pair A",
                "pair_color": "#FF0000"
            },
            {
                "source_image_id": target_image_id,
                "target_image_id": source_image_id,
                "link_id": link_id,
                "text": "Right annotation",
                "coords": {"x": 20, "y": 20, "width": 60, "height": 60},
                "pair_name": "Pair A",
                "pair_color": "#FF0000"
            }
        ]
    }
    
    response = requests.post(
        f"{BASE_URL}/annotations/dual/batch",
        headers=headers,
        json=batch_data
    )
    assert response.status_code == 201
    created = response.json()
    assert len(created) == 2
    
    # 2. Get Dual Annotations for Source Image
    response = requests.get(
        f"{BASE_URL}/annotations/dual",
        headers=headers,
        params={"source_image_id": source_image_id}
    )
    assert response.status_code == 200
    anns = response.json()
    assert len(anns) == 1
    assert anns[0]["source_image_id"] == source_image_id
    assert anns[0]["target_image_id"] == target_image_id
    
    # 3. Get Linked Images
    response = requests.get(
        f"{BASE_URL}/annotations/dual/linked-images/{source_image_id}",
        headers=headers
    )
    assert response.status_code == 200
    linked_ids = response.json()
    assert target_image_id in linked_ids
    
    # 4. Delete by Link ID
    response = requests.delete(
        f"{BASE_URL}/annotations/dual/by-link/{link_id}",
        headers=headers
    )
    assert response.status_code == 204
    
    # 5. Verify deletion
    response = requests.get(
        f"{BASE_URL}/annotations/dual",
        headers=headers,
        params={"source_image_id": source_image_id}
    )
    assert response.status_code == 200
    assert len(response.json()) == 0

def test_thumbnail_access_with_token_query(auth_token, source_image_id):
    """Test accessing thumbnail with token in query param (CORS/Auth check)"""
    # Use token in query param, NO Authorization header
    response = requests.get(
        f"{BASE_URL}/images/{source_image_id}/thumbnail",
        params={"token": auth_token}
    )
    
    # Should be 200 OK (or success)
    # Note: If thumbnail generation fails (no PIL?), it might fallback or error, 
    # but initially it should accept the token.
    # The endpoint returns FileResponse or 500 if PIL missing.
    # If 401, then auth failed.
    
    # If using dummy image content that isn't valid image, thumbnail gen might fail with 500 or fallback?
    # images.py: "If thumbnail generation fails, fallback to original"
    # So it should return 200.
    
    assert response.status_code == 200
    assert response.content == b"fakeimagecontent_source" # Fallback to original
