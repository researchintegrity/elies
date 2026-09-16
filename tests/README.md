"""
README for User Operations Test Suite

This test suite covers complete user workflows for the ELIES User Management System.
"""

# User Operations Test Suite

This comprehensive test suite validates user registration, login, and deletion operations in the ELIES User Management System.

## Test Coverage

### TestUserRegistration
Tests for user account creation and validation:
- ✅ `test_register_user_success` - Successful user registration
- ✅ `test_register_user_missing_required_fields` - Validation of required fields
- ✅ `test_register_user_invalid_email` - Email format validation
- ✅ `test_register_user_short_password` - Password length validation
- ✅ `test_register_duplicate_username` - Duplicate username prevention
- ✅ `test_register_duplicate_email` - Duplicate email prevention

### TestUserLogin
Tests for user authentication:
- ✅ `test_login_with_username` - Login using username
- ✅ `test_login_with_email` - Login using email address
- ✅ `test_login_invalid_username` - Invalid username rejection
- ✅ `test_login_wrong_password` - Wrong password rejection
- ✅ `test_login_returns_valid_token` - JWT token validation

### TestUserDeletion
Tests for user account deletion:
- ✅ `test_delete_user_success` - Successful user deletion
- ✅ `test_delete_user_cannot_login_after_deletion` - Verification of deletion
- ✅ `test_delete_user_without_auth` - Authentication requirement
- ✅ `test_delete_user_with_invalid_token` - Invalid token rejection

### TestUserOperationsIntegration
End-to-end integration tests:
- ✅ `test_complete_user_lifecycle` - Full workflow: register → login → access info → delete
- ✅ `test_multiple_users_independent_operations` - Multiple user isolation

## Setup

### Prerequisites
- MongoDB running locally (default: `mongodb://localhost:27017`)
- Python 3.12+
- Virtual environment with installed dependencies

### Install Test Dependencies
All test dependencies are included in `requirements.txt`:
```bash
pip install -r requirements.txt
```

### Configuration
The test suite uses a separate test database (`elies_system_test`) to avoid affecting production data.

Override test database location by setting environment variables:
```bash
export TEST_MONGODB_URL="mongodb://your-test-server:27017"
```

## Running Tests

### Run All Tests
```bash
pytest tests/test_user_operations.py -v
```

### Run Specific Test Class
```bash
# Run all registration tests
pytest tests/test_user_operations.py::TestUserRegistration -v

# Run all login tests
pytest tests/test_user_operations.py::TestUserLogin -v

# Run all deletion tests
pytest tests/test_user_operations.py::TestUserDeletion -v

# Run integration tests
pytest tests/test_user_operations.py::TestUserOperationsIntegration -v
```

### Run Specific Test
```bash
pytest tests/test_user_operations.py::TestUserRegistration::test_register_user_success -v
```

### Run with Coverage Report
```bash
pytest tests/test_user_operations.py --cov=app --cov-report=html -v
```

### Run with Detailed Output
```bash
pytest tests/test_user_operations.py -vv -s
```

## Test Fixtures

The test suite provides several fixtures (defined in `conftest.py`):

- **`client`** - FastAPI TestClient for making API requests
- **`mongodb_connection`** - MongoDB connection for test database
- **`clean_users_collection`** - Cleans the users collection before each test
- **`test_user_data`** - Sample user data for testing
- **`test_user_data_2`** - Second sample user for multi-user tests

## Example Test Run Output

```
tests/test_user_operations.py::TestUserRegistration::test_register_user_success PASSED
tests/test_user_operations.py::TestUserRegistration::test_register_duplicate_username PASSED
tests/test_user_operations.py::TestUserLogin::test_login_with_username PASSED
tests/test_user_operations.py::TestUserDeletion::test_delete_user_success PASSED
tests/test_user_operations.py::TestUserOperationsIntegration::test_complete_user_lifecycle PASSED

===================== 5 passed in 2.34s =====================
```

## Important Notes

1. **Test Database**: Tests use a separate test database (`elies_system_test`), automatically dropped after each test session.
2. **Isolation**: Each test function gets a clean `users` collection to ensure test independence.
3. **Authentication**: Tests validate JWT token generation and validation.
4. **Password Security**: Tests verify password hashing and validation.
5. **Duplicate Prevention**: Tests ensure username and email uniqueness constraints.

## Debugging Failed Tests

If a test fails, check:
1. **MongoDB Connection**: Ensure MongoDB is running and accessible
2. **Test Data**: Verify test fixtures have valid data
3. **Environment Variables**: Check `.env` file for correct configuration
4. **Database State**: Tests should clean up after themselves; check for orphaned test data

## CI/CD Integration

For CI/CD pipelines, run:
```bash
pytest tests/test_user_operations.py --junitxml=test-results.xml --cov=app --cov-report=xml
```

## Future Enhancements

- [ ] Performance benchmarking tests
- [ ] Concurrent user operations testing
- [ ] Password reset/recovery flow tests
- [ ] Email verification tests
- [ ] Role-based access control tests
