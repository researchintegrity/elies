"""
Test suite for single-image annotations
Verifies creating, listing, and deleting single annotations.
"""
import pytest
import requests
import os

from app.db.mongodb import db_connection

# Needs a running MongoDB (and for some tests Redis/CBIR): run with -m integration
pytestmark = pytest.mark.integration

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
        "username": f"single_ann_{unique_id}",
        "email": f"single_ann_{unique_id}@example.com",
        "password": "Test@Password123",
        "full_name": "Single Ann User"
    }
    response = requests.post(f"{BASE_URL}/auth/register", json=test_user_data)
    assert response.status_code == 200
    return response.json()["access_token"]

@pytest.fixture
def uploaded_image_id(auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    file_content = b"fakeimagecontent"
    files = {"file": ("test_image.jpg", file_content, "image/jpeg")}
    response = requests.post(f"{BASE_URL}/images/upload", headers=headers, files=files)
    assert response.status_code == 201
    return response.json()["_id"]

def test_single_annotations_lifecycle(auth_token, uploaded_image_id):
    headers = {"Authorization": f"Bearer {auth_token}"}
    
    # 1. List - verify empty list works
    response = requests.get(
        f"{BASE_URL}/annotations/single",
        headers=headers,
        params={"image_id": uploaded_image_id}
    )
    assert response.status_code == 200
    assert response.json() == []
    
    # 2. Create
    annotation_data = {
        "image_id": uploaded_image_id,
        "text": "Test annotation",
        "coords": {
            "x": 10, "y": 10, "width": 100, "height": 100,
            "points": [{"x": 10, "y": 10}, {"x": 110, "y": 10}, {"x": 110, "y": 110}, {"x": 10, "y": 110}]
        },
        "type": "manipulation",
        "shape_type": "rectangle"
    }
    
    response = requests.post(
        f"{BASE_URL}/annotations/single",
        headers=headers,
        json=annotation_data
    )
    assert response.status_code == 201
    created_ann = response.json()
    assert created_ann["text"] == "Test annotation"
    
    # 3. List again
    response = requests.get(
        f"{BASE_URL}/annotations/single",
        headers=headers,
        params={"image_id": uploaded_image_id}
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    
    # 4. Delete
    ann_id = created_ann["_id"]
    response = requests.delete(
        f"{BASE_URL}/annotations/single/{ann_id}",
        headers=headers
    )
    assert response.status_code == 204
    
    # 5. Verify deletion
    response = requests.get(
        f"{BASE_URL}/annotations/single/{ann_id}",
        headers=headers
    )
    assert response.status_code == 404
