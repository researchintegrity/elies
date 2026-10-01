"""
End-to-end tests for Jobs API and Pub/Sub notifications.

Tests:
1. GET /jobs/stats - Job statistics
2. GET /jobs - Paginated job listing with filters
3. GET /jobs/{job_id} - Single job details
4. job_logger service functions
5. Pub/sub notification pattern
6. SSE stream endpoint
"""
import pytest
import requests
import time
import os

# Configuration
BASE_URL = os.getenv("API_URL", "http://localhost:8000")
USERNAME = "test_jobs_user"
PASSWORD = "TestPassword123"


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="module")
def auth_token():
    """Register and login a test user, return access token"""
    register_data = {
        "username": USERNAME,
        "email": f"{USERNAME}@example.com",
        "password": PASSWORD,
        "full_name": "Jobs Test User"
    }
    try:
        requests.post(f"{BASE_URL}/auth/register", json=register_data)
    except Exception:
        pass

    login_data = {"username": USERNAME, "password": PASSWORD}
    response = requests.post(f"{BASE_URL}/auth/login", data=login_data)
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def auth_headers(auth_token):
    """Return authorization headers"""
    return {"Authorization": f"Bearer {auth_token}"}


@pytest.fixture(scope="module")
def user_id(auth_token):
    """Get the user ID from the token"""
    headers = {"Authorization": f"Bearer {auth_token}"}
    response = requests.get(f"{BASE_URL}/users/me", headers=headers)
    assert response.status_code == 200
    return response.json()["_id"]


# ============================================================================
# UNIT TESTS - job_logger service
# ============================================================================

class TestJobLoggerService:
    """Unit tests for job_logger service functions"""
    
    def test_create_job_log(self, user_id):
        """Test creating a job log entry"""
        from app.services.job_logger import create_job_log, get_job
        from app.schemas import JobType
        from app.db.mongodb import get_jobs_collection
        
        job_id = create_job_log(
            user_id=user_id,
            job_type=JobType.TRUFOR,
            title="Test Job Creation",
            celery_task_id="test-celery-123",
            input_data={"test": True}
        )
        
        assert job_id is not None
        assert job_id.startswith("job_")
        
        # Verify job exists in DB
        job = get_job(job_id, user_id)
        assert job is not None
        assert job["status"] == "pending"
        assert job["title"] == "Test Job Creation"
        assert job["job_type"] == "trufor"
        
        # Cleanup
        get_jobs_collection().delete_one({"_id": job_id})
    
    def test_update_job_progress(self, user_id):
        """Test updating job progress"""
        from app.services.job_logger import create_job_log, update_job_progress, get_job
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        job_id = create_job_log(
            user_id=user_id,
            job_type=JobType.COPY_MOVE_SINGLE,
            title="Test Progress Update"
        )
        
        # Update progress
        update_job_progress(
            job_id=job_id,
            user_id=user_id,
            status=JobStatus.PROCESSING,
            progress_percent=50.0,
            current_step="Halfway done"
        )
        
        # Verify updates
        job = get_job(job_id, user_id)
        assert job["status"] == "processing"
        assert job["progress_percent"] == 50.0
        assert job["current_step"] == "Halfway done"
        assert job["started_at"] is not None
        
        # Cleanup
        get_jobs_collection().delete_one({"_id": job_id})
    
    def test_complete_job_success(self, user_id):
        """Test completing a job successfully"""
        from app.services.job_logger import create_job_log, complete_job, get_job
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        job_id = create_job_log(
            user_id=user_id,
            job_type=JobType.PROVENANCE,
            title="Test Complete Success"
        )
        
        complete_job(
            job_id=job_id,
            user_id=user_id,
            status=JobStatus.COMPLETED,
            output_data={"result": "success"}
        )
        
        job = get_job(job_id, user_id)
        assert job["status"] == "completed"
        assert job["progress_percent"] == 100.0
        assert job["completed_at"] is not None
        assert job["expires_at"] is not None
        assert job["output_data"]["result"] == "success"
        
        # Cleanup
        get_jobs_collection().delete_one({"_id": job_id})
    
    def test_complete_job_failure(self, user_id):
        """Test completing a job with failure"""
        from app.services.job_logger import create_job_log, complete_job, get_job
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        job_id = create_job_log(
            user_id=user_id,
            job_type=JobType.WATERMARK_REMOVAL,
            title="Test Complete Failure"
        )
        
        complete_job(
            job_id=job_id,
            user_id=user_id,
            status=JobStatus.FAILED,
            errors=["Something went wrong", "Another error"]
        )
        
        job = get_job(job_id, user_id)
        assert job["status"] == "failed"
        assert len(job["errors"]) == 2
        assert "Something went wrong" in job["errors"]
        
        # Cleanup
        get_jobs_collection().delete_one({"_id": job_id})


# ============================================================================
# API TESTS - /jobs endpoints
# ============================================================================

class TestJobsAPI:
    """E2E tests for /jobs API endpoints"""
    
    def test_get_jobs_stats_empty(self, auth_headers):
        """Test getting stats for user with no jobs"""
        response = requests.get(f"{BASE_URL}/jobs/stats", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert "total_jobs" in data
        assert "pending" in data
        assert "processing" in data
        assert "completed" in data
        assert "failed" in data
        assert "by_type" in data
    
    def test_get_jobs_list_empty(self, auth_headers):
        """Test getting empty job list"""
        response = requests.get(f"{BASE_URL}/jobs", headers=auth_headers)
        assert response.status_code == 200
        
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "per_page" in data
        assert "total_pages" in data
        assert "has_next" in data
        assert "has_prev" in data
    
    def test_get_jobs_list_with_filters(self, auth_headers, user_id):
        """Test job list with filters"""
        from app.services.job_logger import create_job_log, complete_job
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        # Create test jobs
        job1_id = create_job_log(user_id, JobType.TRUFOR, "Filter Test 1")
        job2_id = create_job_log(user_id, JobType.COPY_MOVE_SINGLE, "Filter Test 2")
        complete_job(job2_id, user_id, JobStatus.COMPLETED)
        
        try:
            # Filter by job_type
            response = requests.get(
                f"{BASE_URL}/jobs?job_type=trufor",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            for item in data["items"]:
                assert item["job_type"] == "trufor"
            
            # Filter by status
            response = requests.get(
                f"{BASE_URL}/jobs?status=completed",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            for item in data["items"]:
                assert item["status"] == "completed"
        finally:
            # Cleanup
            get_jobs_collection().delete_many({"_id": {"$in": [job1_id, job2_id]}})
    
    def test_get_job_by_id(self, auth_headers, user_id):
        """Test getting a specific job by ID"""
        from app.services.job_logger import create_job_log
        from app.schemas import JobType
        from app.db.mongodb import get_jobs_collection
        
        job_id = create_job_log(
            user_id=user_id,
            job_type=JobType.PANEL_EXTRACTION,
            title="Get By ID Test"
        )
        
        try:
            response = requests.get(
                f"{BASE_URL}/jobs/{job_id}",
                headers=auth_headers
            )
            assert response.status_code == 200
            
            data = response.json()
            assert data["job_id"] == job_id
            assert data["job_type"] == "panel_extraction"
            assert data["title"] == "Get By ID Test"
        finally:
            get_jobs_collection().delete_one({"_id": job_id})
    
    def test_get_job_not_found(self, auth_headers):
        """Test 404 for non-existent job"""
        response = requests.get(
            f"{BASE_URL}/jobs/job_nonexistent_12345_abcd",
            headers=auth_headers
        )
        assert response.status_code == 404
    
    def test_jobs_pagination(self, auth_headers, user_id):
        """Test pagination works correctly"""
        from app.services.job_logger import create_job_log
        from app.schemas import JobType
        from app.db.mongodb import get_jobs_collection
        
        # Create 5 test jobs
        job_ids = []
        for i in range(5):
            job_id = create_job_log(user_id, JobType.TRUFOR, f"Pagination Test {i}")
            job_ids.append(job_id)
        
        try:
            # Get first page with 2 items
            response = requests.get(
                f"{BASE_URL}/jobs?page=1&per_page=2",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data["items"]) == 2
            assert data["has_next"] is True
            assert data["has_prev"] is False
            
            # Get second page
            response = requests.get(
                f"{BASE_URL}/jobs?page=2&per_page=2",
                headers=auth_headers
            )
            assert response.status_code == 200
            data = response.json()
            assert len(data["items"]) == 2
            assert data["has_prev"] is True
        finally:
            get_jobs_collection().delete_many({"_id": {"$in": job_ids}})


# ============================================================================
# PUB/SUB NOTIFICATION TESTS
# ============================================================================

class TestPubSubNotifications:
    """Tests for the pub/sub notification pattern"""
    
    def test_subscribe_unsubscribe(self, user_id):
        """Test subscribe and unsubscribe functions"""
        from app.services.job_logger import subscribe, unsubscribe, _subscribers
        
        queue = subscribe(user_id)
        assert queue is not None
        assert user_id in _subscribers
        assert queue in _subscribers[user_id]
        
        unsubscribe(user_id, queue)
        assert user_id not in _subscribers or queue not in _subscribers.get(user_id, [])
    
    def test_notification_on_job_create(self, user_id):
        """Test notification is sent when job is created"""
        from app.services.job_logger import subscribe, unsubscribe, create_job_log
        from app.schemas import JobType
        from app.db.mongodb import get_jobs_collection
        
        queue = subscribe(user_id)
        
        try:
            job_id = create_job_log(
                user_id=user_id,
                job_type=JobType.TRUFOR,
                title="Notification Test"
            )
            
            # Check notification was sent
            assert not queue.empty()
            notification = queue.get_nowait()
            assert notification["event"] == "job_started"
            assert notification["job_id"] == job_id
            assert notification["job_type"] == "trufor"
            
            # Cleanup
            get_jobs_collection().delete_one({"_id": job_id})
        finally:
            unsubscribe(user_id, queue)
    
    def test_notification_on_job_complete(self, user_id):
        """Test notification is sent when job completes"""
        from app.services.job_logger import (
            subscribe, unsubscribe, create_job_log, complete_job
        )
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        queue = subscribe(user_id)
        
        try:
            job_id = create_job_log(user_id, JobType.TRUFOR, "Complete Notification Test")
            
            # Clear create notification
            queue.get_nowait()
            
            complete_job(job_id, user_id, JobStatus.COMPLETED, {"result": "ok"})
            
            # Check completion notification
            assert not queue.empty()
            notification = queue.get_nowait()
            assert notification["event"] == "job_completed"
            assert notification["job_id"] == job_id
            assert notification["status"] == "completed"
            
            # Cleanup
            get_jobs_collection().delete_one({"_id": job_id})
        finally:
            unsubscribe(user_id, queue)
    
    def test_notification_on_job_failed(self, user_id):
        """Test notification is sent when job fails"""
        from app.services.job_logger import (
            subscribe, unsubscribe, create_job_log, complete_job
        )
        from app.schemas import JobType, JobStatus
        from app.db.mongodb import get_jobs_collection
        
        queue = subscribe(user_id)
        
        try:
            job_id = create_job_log(user_id, JobType.TRUFOR, "Failure Notification Test")
            queue.get_nowait()  # Clear create notification
            
            complete_job(job_id, user_id, JobStatus.FAILED, errors=["Test error"])
            
            notification = queue.get_nowait()
            assert notification["event"] == "job_failed"
            assert notification["error"] == "Test error"
            
            # Cleanup
            get_jobs_collection().delete_one({"_id": job_id})
        finally:
            unsubscribe(user_id, queue)


# ============================================================================
# SSE STREAM TESTS
# ============================================================================

class TestSSEStream:
    """Tests for SSE streaming endpoint"""
    
    def test_sse_stream_endpoint_exists(self, auth_headers):
        """Test SSE stream endpoint is accessible"""
        # Use a short timeout since SSE streams are long-lived
        try:
            response = requests.get(
                f"{BASE_URL}/jobs/stream",
                headers=auth_headers,
                stream=True,
                timeout=2
            )
            assert response.status_code == 200
            assert "text/event-stream" in response.headers.get("Content-Type", "")
        except requests.exceptions.ReadTimeout:
            # Expected - SSE streams don't end
            pass


# ============================================================================
# INTEGRATION TESTS - Jobs with actual Celery tasks
# ============================================================================

class TestJobsIntegration:
    """Integration tests for jobs with actual analysis tasks"""
    
    @pytest.fixture
    def test_image_file(self):
        """Create a dummy image file for testing"""
        test_path = "test_jobs_integration_image.jpg"
        if not os.path.exists(test_path):
            from PIL import Image, ImageDraw
            img = Image.new('RGB', (200, 200), color='blue')
            d = ImageDraw.Draw(img)
            d.text((10, 10), "Jobs Test", fill=(255, 255, 255))
            img.save(test_path)
        
        yield test_path
        
        if os.path.exists(test_path):
            os.remove(test_path)
    
    @pytest.fixture
    def uploaded_image_id(self, auth_headers, test_image_file):
        """Upload an image and return its ID"""
        with open(test_image_file, "rb") as f:
            files = {"file": (os.path.basename(test_image_file), f, "image/jpeg")}
            response = requests.post(
                f"{BASE_URL}/images/upload",
                headers=auth_headers,
                files=files
            )
        assert response.status_code == 201, f"Upload failed: {response.text}"
        data = response.json()
        return data.get("id") or data.get("_id")
    
    def test_copy_move_creates_job(self, auth_headers, uploaded_image_id, user_id):
        """Test that triggering copy-move creates a job entry"""
        from app.db.mongodb import get_jobs_collection
        
        # Get initial job count
        initial_count = get_jobs_collection().count_documents({"user_id": user_id})
        
        # Trigger copy-move analysis
        payload = {"image_id": uploaded_image_id, "method": "dense", "dense_method": 2}
        response = requests.post(
            f"{BASE_URL}/analyses/copy-move/single",
            json=payload,
            headers=auth_headers
        )
        assert response.status_code == 202
        
        # Wait for job to be created
        time.sleep(2)
        
        # Check job was created
        current_count = get_jobs_collection().count_documents({"user_id": user_id})
        assert current_count > initial_count
        
        # Find the new job
        job = get_jobs_collection().find_one({
            "user_id": user_id,
            "job_type": "copy_move_single"
        }, sort=[("created_at", -1)])
        
        assert job is not None
        assert job["title"].startswith("Copy-Move Detection")
    
    def test_job_completes_after_analysis(self, auth_headers, uploaded_image_id, user_id):
        """Test that job is marked complete when analysis finishes"""
        from app.db.mongodb import get_jobs_collection
        
        # Trigger analysis
        payload = {"image_id": uploaded_image_id, "method": "dense", "dense_method": 2}
        response = requests.post(
            f"{BASE_URL}/analyses/copy-move/single",
            json=payload,
            headers=auth_headers
        )
        analysis_id = response.json().get("analysis_id")
        
        # Poll for completion
        for _ in range(30):
            time.sleep(2)
            response = requests.get(
                f"{BASE_URL}/analyses/{analysis_id}",
                headers=auth_headers
            )
            if response.json().get("status") in ["completed", "failed"]:
                break
        
        # Check job status matches
        job = get_jobs_collection().find_one({
            "user_id": user_id,
            "job_type": "copy_move_single"
        }, sort=[("created_at", -1)])
        
        assert job is not None
        assert job["status"] in ["completed", "failed"]
        assert job["completed_at"] is not None
