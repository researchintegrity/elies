"""
Test suite for user operations: registration, login, and deletion
"""
import pytest
import requests
import os

from app.db.mongodb import get_users_collection, db_connection

# Calls a running API server (API_URL, default http://localhost:8000): run with -m e2e
pytestmark = pytest.mark.e2e

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


@pytest.fixture(autouse=True)
def cleanup_database():
    """Cleanup database collections after each test"""
    yield
    # Clean up collections
    try:
        users_col = get_users_collection()
        # Delete test users (those with usernames starting with test_)
        users_col.delete_many({"username": {"$regex": "^test_"}})
    except Exception:
        # if nothing to clean up or error occurs, just pass
        pass


class TestUserRegistration:
    """Tests for user registration"""

    def test_register_user_success(self):
        """Test successful user registration"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        response = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )

        assert response.status_code == 200
        data = response.json()

        # Verify token response
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user" in data
        assert "expires_in" in data

        # Verify user data
        user = data["user"]
        assert user["username"] == test_user_data["username"]
        assert user["email"] == test_user_data["email"]
        assert user["full_name"] == test_user_data["full_name"]
        assert user["is_active"] is True

    def test_register_user_missing_required_fields(self):
        """Test registration with missing required fields"""
        incomplete_data = {
            "username": "testuser",
            # missing email and password
        }

        response = requests.post(
            f"{BASE_URL}/auth/register",
            json=incomplete_data
        )

        assert response.status_code == 422

    def test_register_user_invalid_email(self):
        """Test registration with invalid email format"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        invalid_data = {
            "username": f"testuser_{unique_id}",
            "email": "invalid-email",
            "password": "Test@Password123",
            "full_name": "Test User"
        }

        response = requests.post(
            f"{BASE_URL}/auth/register",
            json=invalid_data
        )

        assert response.status_code == 422

    def test_register_user_short_password(self):
        """Test registration with password less than 4 characters"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        short_pass_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "ab",  # Less than 4 characters
            "full_name": "Test User"
        }

        response = requests.post(
            f"{BASE_URL}/auth/register",
            json=short_pass_data
        )

        assert response.status_code == 422

    def test_register_duplicate_username(self):
        """Test registration with duplicate username"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        
        # Register first user
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        response1 = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )
        assert response1.status_code == 200

        # Try to register with same username but different email
        duplicate_data = {
            "username": test_user_data["username"],  # Same username
            "email": f"different_{unique_id}@example.com",
            "password": "AnotherPass123",
            "full_name": "Different User"
        }

        response2 = requests.post(
            f"{BASE_URL}/auth/register",
            json=duplicate_data
        )

        assert response2.status_code == 400
        assert "already registered" in response2.json()["detail"]

    def test_register_duplicate_email(self):
        """Test registration with duplicate email"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        
        # Register first user
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        response1 = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )
        assert response1.status_code == 200

        # Try to register with same email but different username
        duplicate_data = {
            "username": f"differentuser_{unique_id}",
            "email": test_user_data["email"],  # Same email
            "password": "AnotherPass123",
            "full_name": "Different User"
        }

        response2 = requests.post(
            f"{BASE_URL}/auth/register",
            json=duplicate_data
        )

        assert response2.status_code == 400
        assert "already registered" in response2.json()["detail"]


class TestUserLogin:
    """Tests for user login"""

    def test_login_with_username(self):
        """Test login using username"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user first
        requests.post(f"{BASE_URL}/auth/register", json=test_user_data)

        # Login with username
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": test_user_data["password"]
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == test_user_data["username"]
        assert data["user"]["email"] == test_user_data["email"]

    def test_login_with_email(self):
        """Test login using email instead of username"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user first
        requests.post(f"{BASE_URL}/auth/register", json=test_user_data)

        # Login with email
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["email"],  # Use email as username
                "password": test_user_data["password"]
            }
        )

        assert response.status_code == 200
        data = response.json()

        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["username"] == test_user_data["username"]

    def test_login_invalid_username(self):
        """Test login with non-existent username"""
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": "nonexistent",
                "password": "SomePassword123"
            }
        )

        assert response.status_code == 401
        assert "Invalid username or password" in response.json()["detail"]

    def test_login_wrong_password(self):
        """Test login with incorrect password"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user first
        requests.post(f"{BASE_URL}/auth/register", json=test_user_data)

        # Login with wrong password
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": "WrongPassword123"
            }
        )

        assert response.status_code == 401
        assert "Invalid username or password" in response.json()["detail"]

    def test_login_returns_valid_token(self):
        """Test that login returns a valid JWT token"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user
        requests.post(f"{BASE_URL}/auth/register", json=test_user_data)

        # Login
        response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": test_user_data["password"]
            }
        )

        assert response.status_code == 200
        token = response.json()["access_token"]

        # Verify token can be used to access protected endpoint
        auth_response = requests.get(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )

        assert auth_response.status_code == 200
        assert auth_response.json()["username"] == test_user_data["username"]


class TestUserDeletion:
    """Tests for user deletion"""

    def test_delete_user_success(self):
        """Test successful user deletion"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user
        register_response = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )
        assert register_response.status_code == 200
        token = register_response.json()["access_token"]

        # Delete user
        response = requests.delete(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "deleted" in data["message"].lower()

    def test_delete_user_cannot_login_after_deletion(self):
        """Test that deleted user cannot login"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Register user
        register_response = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )
        token = register_response.json()["access_token"]

        # Delete user
        delete_response = requests.delete(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert delete_response.status_code == 200

        # Try to login with deleted user
        login_response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": test_user_data["password"]
            }
        )

        assert login_response.status_code == 401

    def test_delete_user_without_auth(self):
        """Test deletion without authentication fails"""
        response = requests.delete(f"{BASE_URL}/users/me")

        assert response.status_code == 401

    def test_delete_user_with_invalid_token(self):
        """Test deletion with invalid token fails"""
        response = requests.delete(
            f"{BASE_URL}/users/me",
            headers={"Authorization": "Bearer invalid_token_xyz"}
        )

        assert response.status_code == 401


class TestUserOperationsIntegration:
    """Integration tests for complete user workflows"""

    def test_complete_user_lifecycle(self):
        """Test complete user lifecycle: register -> login -> delete"""
        import uuid
        unique_id = str(uuid.uuid4())[:8]
        test_user_data = {
            "username": f"testuser_{unique_id}",
            "email": f"testuser_{unique_id}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User"
        }
        
        # Step 1: Register user
        register_response = requests.post(
            f"{BASE_URL}/auth/register",
            json=test_user_data
        )
        assert register_response.status_code == 200
        register_data = register_response.json()
        token = register_data["access_token"]

        assert register_data["user"]["username"] == test_user_data["username"]
        assert register_data["user"]["email"] == test_user_data["email"]

        # Step 2: Login with registered user
        login_response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": test_user_data["password"]
            }
        )
        assert login_response.status_code == 200
        login_data = login_response.json()

        assert login_data["user"]["username"] == test_user_data["username"]
        assert login_data["access_token"] != ""

        # Step 3: Get current user info
        user_info_response = requests.get(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert user_info_response.status_code == 200
        user_info = user_info_response.json()

        assert user_info["username"] == test_user_data["username"]
        assert user_info["email"] == test_user_data["email"]

        # Step 4: Delete user
        delete_response = requests.delete(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert delete_response.status_code == 200

        # Step 5: Verify user is deleted (cannot login)
        final_login_response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data["username"],
                "password": test_user_data["password"]
            }
        )
        assert final_login_response.status_code == 401

    def test_multiple_users_independent_operations(self):
        """Test that multiple users can operate independently"""
        import uuid
        unique_id1 = str(uuid.uuid4())[:8]
        unique_id2 = str(uuid.uuid4())[:8]
        
        test_user_data = {
            "username": f"testuser_{unique_id1}",
            "email": f"testuser_{unique_id1}@example.com",
            "password": "Test@Password123",
            "full_name": "Test User 1"
        }
        test_user_data_2 = {
            "username": f"testuser_{unique_id2}",
            "email": f"testuser_{unique_id2}@example.com",
            "password": "Test@Password456",
            "full_name": "Test User 2"
        }
        
        # Register first user
        response1 = requests.post(f"{BASE_URL}/auth/register", json=test_user_data)
        assert response1.status_code == 200
        token1 = response1.json()["access_token"]

        # Register second user
        response2 = requests.post(f"{BASE_URL}/auth/register", json=test_user_data_2)
        assert response2.status_code == 200
        token2 = response2.json()["access_token"]

        # Verify each user can access their own info
        user1_info = requests.get(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token1}"}
        )
        assert user1_info.json()["username"] == test_user_data["username"]

        user2_info = requests.get(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token2}"}
        )
        assert user2_info.json()["username"] == test_user_data_2["username"]

        # Delete first user
        delete_response = requests.delete(
            f"{BASE_URL}/users/me",
            headers={"Authorization": f"Bearer {token1}"}
        )
        assert delete_response.status_code == 200

        # Verify second user still exists and can login
        login_response = requests.post(
            f"{BASE_URL}/auth/login",
            data={
                "username": test_user_data_2["username"],
                "password": test_user_data_2["password"]
            }
        )
        assert login_response.status_code == 200
        assert login_response.json()["user"]["username"] == test_user_data_2["username"]
