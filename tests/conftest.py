"""
Test configuration and fixtures for pytest
"""
import pytest
import os
from dotenv import load_dotenv

# Load test environment variables immediately, before importing app modules

current_dir = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(current_dir, 'test.env')
load_dotenv(dotenv_path)
from fastapi.testclient import TestClient
from app.main import app
from app.db.mongodb import db_connection, get_users_collection

# Using separate test database to avoid conflicts with main app
# NOTE: Now using main MongoDB connection with test database name
TEST_DATABASE_NAME = "elies_system"


@pytest.fixture(scope="session")
def mongodb_connection():
    """Set up MongoDB connection for tests"""
    # Use the main MongoDB connection but with a test database
    original_db = os.getenv("DATABASE_NAME")
    
    os.environ["DATABASE_NAME"] = TEST_DATABASE_NAME
    
    # Reinitialize connection with test database
    db_connection._client = None
    db_connection._db = None
    db_connection.connect()
    
    yield db_connection
    
    # Cleanup: drop test database and restore original settings
    try:
        client = db_connection._client
        client.drop_database(TEST_DATABASE_NAME)
        db_connection.disconnect()
    except Exception as e:
        print(f"Cleanup error: {e}")
    
    # Restore original settings
    if original_db:
        os.environ["DATABASE_NAME"] = original_db


@pytest.fixture(scope="function")
def client(mongodb_connection):
    """FastAPI TestClient for API testing"""
    return TestClient(app)


@pytest.fixture(scope="function")
def clean_users_collection(mongodb_connection):
    """Clean users collection before each test"""
    collection = get_users_collection()
    collection.drop()
    collection.create_index("username", unique=True)
    collection.create_index("email", unique=True)
    yield collection
    # Cleanup after test
    collection.drop()


@pytest.fixture
def test_user_data():
    """Test user data for registration"""
    return {
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "Test@Password123",
        "full_name": "Test User"
    }


@pytest.fixture
def test_user_data_2():
    """Second test user data for multi-user tests"""
    return {
        "username": "testuser2",
        "email": "testuser2@example.com",
        "password": "Test@Password456",
        "full_name": "Test User 2"
    }
