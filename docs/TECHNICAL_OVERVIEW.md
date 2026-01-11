# ELIS Scientific Image Analysis System

ELIS is an open-source scientific document and image integrity analysis system.

## Features

- User authentication and management with JWT tokens
- Document upload and management with storage quotas
- Asynchronous PDF processing with Celery workers
- Image extraction and retrieval
- **Panel extraction from scientific images using YOLO-based panel-extractor**
- Full test coverage with 62+ passing tests
- Docker containerization for easy deployment
- MongoDB for persistent storage
- Redis for task queue and caching

## Project Structure

```
elis-system/
├── app/
│   ├── main.py                      # FastAPI application
│   ├── schemas.py                   # Pydantic validation models
│   ├── celery_config.py             # Celery configuration
│   ├── routes/                      # API endpoints
│   │   ├── auth.py                  # Authentication
│   │   ├── users.py                 # User management
│   │   ├── documents.py             # Document upload/management
│   │   ├── images.py                # Image & panel management
│   │   ├── annotations.py           # Annotations
│   │   └── api.py                   # General API
│   ├── tasks/                       # Celery async tasks
│   │   ├── image_extraction.py      # PDF → images extraction
│   │   └── panel_extraction.py      # Panel extraction from images
│   ├── services/                    # Business logic layer
│   │   ├── document_service.py      # Document operations
│   │   ├── image_service.py         # Image operations
│   │   └── panel_extraction_service.py  # Panel extraction logic
│   ├── db/                          # Database layer
│   │   └── mongodb.py               # MongoDB connection
│   ├── config/                      # Configuration
│   │   ├── settings.py              # App settings
│   │   └── storage_quota.py         # Storage management
│   └── utils/                       # Utilities
│       ├── security.py              # JWT, password hashing
│       ├── file_storage.py          # File operations
│       ├── docker_extraction.py     # PDF extraction Docker wrapper
│       └── docker_panel_extractor.py # Panel extraction Docker wrapper
├── tests/                           # Test suite (62+ tests)
│   ├── conftest.py                  # Pytest configuration
│   ├── test_user_operations.py      # User management tests
│   ├── test_document_upload.py      # Document & image tests
│   ├── test_docker_extraction.py    # Docker integration tests
│   ├── test_panel_extraction.py     # Panel extraction unit tests
│   ├── test_panel_extraction_docker.py  # Panel extraction Docker tests
│   └── test_panel_extraction_e2e.py # Panel extraction end-to-end tests
├── system_modules/                  # External analysis modules
│   ├── cbir-system/                 # Content-Based Image Retrieval
│   ├── copy-move-detection/         # Copy-move forgery detection
│   ├── front-end-platform/          # Frontend application
│   ├── panel-extractor/             # Panel extraction from images
│   ├── pdf-image-extraction/        # PDF to image extraction
│   ├── provenance-analysis/         # Document provenance analysis
│   └── TruFor/                      # Image manipulation detection
├── datasets/                        # Sample datasets for testing
├── docker-compose.yml               # Multi-container orchestration
├── Dockerfile                       # API container
├── Dockerfile.worker                # Celery worker container
├── requirements.txt                 # Python dependencies
├── pytest.ini                       # Pytest configuration
└── README.md                        # This file
```

## System Modules

The ELIS system integrates several specialized analysis modules, each implemented as a separate repository in the same GitHub organization (`researchintegrity`). These modules provide the core AI/ML capabilities for scientific image and document analysis.

### Available Modules

- **cbir-system**: Content-Based Image Retrieval for similarity search
- **copy-move-detection**: Detection of copy-move image forgeries
- **copy-move-detection-keypoint**: Advanced keypoint-based copy-move detection
- **front-end-platform**: Web-based user interface for the system
- **panel-extractor**: YOLO-based panel extraction from scientific images
- **pdf-image-extraction**: PDF document to image conversion
- **provenance-analysis**: Document provenance and integrity analysis
- **TruFor**: Image manipulation and tampering detection

### Cloning Modules

For development or to build custom Docker images, you can clone the individual modules into the `system_modules/` directory:

```bash
# Clone all modules (run from project root)
cd system_modules
git clone https://github.com/researchintegrity/cbir-system.git
git clone https://github.com/researchintegrity/copy-move-detection.git
git clone https://github.com/researchintegrity/front-end-platform.git
git clone https://github.com/researchintegrity/panel-extractor.git
git clone https://github.com/researchintegrity/pdf-image-extraction.git
git clone https://github.com/researchintegrity/provenance-analysis.git
git clone https://github.com/researchintegrity/TruFor.git
git clone https://github.com/researchintegrity/copy-move-detection-keypoint.git
```

Note: The system is designed to work with pre-built Docker images for production use. Cloning modules is only necessary for development or custom builds.

## Getting Started

### Prerequisites

- Docker and Docker Compose (for containerized setup)
- Python 3.12+
- Git

### Quick Start with Docker (Recommended)

The easiest way to get the system running is with Docker Compose. This starts all required services automatically, including the API, Database, Celery Workers, and Microservices (CBIR, Provenance).

1. Clone and navigate to the repository:

```bash
git clone <repository-url>
cd elis-system
```

2. **Build the Tool Images (First Time Only)**:
   The system relies on several specialized Docker images for tasks like PDF extraction, watermark removal, and copy-move detection. You need to build these images first:

```bash
docker-compose --profile tools build
```

3. **Start the Main System**:
   Start the API, Database, Workers, and Microservices:

```bash
docker-compose up -d
```

4. **Verify Services**:

```bash
docker-compose ps
```

### Accessing Services

Once the system is running, you can access the following interfaces:

| Service | URL | Description |
|---------|-----|-------------|
| **API Documentation** | [http://localhost:8000/docs](http://localhost:8000/docs) | Swagger UI for the Backend API |
| **Milvus GUI (Attu)** | [http://localhost:3322](http://localhost:3322) | Visual interface for the Vector Database |
| **Celery Monitor (Flower)** | [http://localhost:5555](http://localhost:5555) | Monitor async tasks and workers |
| **Provenance Service** | [http://localhost:8002/docs](http://localhost:8002/docs) | API docs for the Provenance Microservice |
| **CBIR Service** | [http://localhost:8001/docs](http://localhost:8001/docs) | API docs for the CBIR Microservice |
| **MinIO Console** | [http://localhost:9001](http://localhost:9001) | Object storage browser (User/Pass: `minioadmin`) |

### Stopping the System

To stop all running services:

```bash
docker-compose down
```

To stop and remove volumes (WARNING: deletes database data):

```bash
docker-compose down -v
```

All services will be available once running:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- Flower Dashboard: http://localhost:5555
- MongoDB: localhost:27017
- Redis: localhost:6379

### Manual Setup (Local Development)

If you prefer to run services locally without Docker, follow these steps:

1. Clone the repository:

```bash
git clone <repository-url>
cd elis-system
```

2. Create and activate a virtual environment:

```bash
python -m venv uvenv
source uvenv/bin/activate  # On Windows: uvenv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Ensure required services are running:

- MongoDB: Start MongoDB service or use a remote instance
- Redis: Start Redis server for Celery task queue

5. Configure environment variables:

```bash
cp .env.example .env
# Edit .env with your settings
```

6. Start the services in separate terminals:

Terminal 1 - FastAPI:
```bash
uvicorn app.main:app --reload
```

Terminal 2 - Celery Worker:
```bash
celery -A app.celery_config worker -l info
```

Terminal 3 - Flower Monitoring (optional):
```bash
celery -A app.celery_config flower --port=5555
```

The API will be available at http://localhost:8000

### Technology Stack

The system uses several key technologies:

**Web Framework**
- FastAPI: Modern Python web framework with automatic API documentation

**Database**
- MongoDB: NoSQL database for flexible document storage
- PyMongo: MongoDB Python driver for database operations

**Asynchronous Task Processing**
- Celery: Distributed task queue for background jobs
- Redis: Message broker and result backend for Celery

**Authentication & Security**
- PyJWT: JWT token generation and validation
- Passlib & Bcrypt: Secure password hashing

**File Handling**
- python-multipart: Form data and file upload handling

**Validation & Documentation**
- Pydantic: Data validation and automatic schema generation
- email-validator: Email address validation

**Testing**
- Pytest: Testing framework
- FastAPI TestClient: API testing utilities

### Docker Architecture

The system runs multiple containers orchestrated by Docker Compose:

**API Container**
- Runs FastAPI application on port 8000
- Mounts workspace volume for file storage
- Depends on MongoDB and Redis

**Worker Containers (2 instances)**
- Run Celery workers for background PDF processing
- Access shared workspace volume
- Connect to Redis for task queue
- Connect to MongoDB for result storage

**MongoDB**
- Main database for user data and documents
- Runs on port 27017

**MongoDB Test Database**
- Isolated database for test execution
- Runs on port 27018
- Unauthenticated for local development

**Redis**
- Message broker for Celery
- Result backend for task status
- Runs on port 6379

**Flower**
- Celery monitoring dashboard
- Real-time view of background tasks
- Available on port 5555

## API Documentation

Interactive API documentation is available at [Swagger UI](http://localhost:8000/docs) or [ReDoc](http://localhost:8000/redoc).

### Authentication Endpoints

#### Register User

```http
POST /auth/register
Content-Type: application/json

{
  "username": "john_doe",
  "email": "john@example.com",
  "password": "SecurePassword123",
  "full_name": "John Doe"
}
```

Response includes access token and user details.

#### Login User

```http
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=john_doe&password=SecurePassword123
```

Returns access token for authenticated requests.

### User Management Endpoints

#### Get Current User Profile

```http
GET /users/me
Authorization: Bearer <access_token>
```

#### Update User Profile

```http
PUT /users/me
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "full_name": "John Updated",
  "email": "newemail@example.com"
}
```

#### Delete User Account

```http
DELETE /users/me
Authorization: Bearer <access_token>
```

Removes user and all associated data.

### Document Management Endpoints

#### Upload Document

```http
POST /documents/upload
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

file: <PDF file>
```

Returns document ID and extraction status (pending or completed).

#### List Documents

```http
GET /documents
Authorization: Bearer <access_token>
```

Returns paginated list of user documents with storage quota information.

#### Get Document Details

```http
GET /documents/{document_id}
Authorization: Bearer <access_token>
```

#### Delete Document

```http
DELETE /documents/{document_id}
Authorization: Bearer <access_token>
```

### Image Management Endpoints

#### List Images

```http
GET /images
Authorization: Bearer <access_token>
```

Returns paginated list of user images with quota information.

#### Get Image Details

```http
GET /images/{image_id}
Authorization: Bearer <access_token>
```

#### Delete Image

```http
DELETE /images/{image_id}
Authorization: Bearer <access_token>
```

### Panel Extraction Endpoints

Panel extraction identifies and extracts individual scientific figures/panels from images using YOLO-based deep learning models.

#### Initiate Panel Extraction

```http
POST /images/extract-panels
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "image_ids": ["image_id_1", "image_id_2"],
  "model_type": "default"
}
```

Returns:
```json
{
  "task_id": "celery-task-uuid",
  "status": "PENDING",
  "image_ids": ["image_id_1", "image_id_2"],
  "message": "Panel extraction task queued"
}
```

#### Check Panel Extraction Status

```http
GET /images/extract-panels/status/{task_id}
Authorization: Bearer <access_token>
```

Polling endpoint returns:
- `PENDING`: Task still processing
- `SUCCESS`: Completed, includes `extracted_panels` array
- `FAILURE`: Failed, includes `error` message

Response when completed:
```json
{
  "task_id": "celery-task-uuid",
  "status": "SUCCESS",
  "image_ids": ["image_id_1"],
  "extracted_panels_count": 3,
  "extracted_panels": [
    {
      "id": "panel_object_id",
      "filename": "panel_00001.png",
      "file_path": "/workspace/user_id/images/panels/panel_00001.png",
      "file_size": 15234,
      "source_type": "panel",
      "source_image_id": "image_id_1",
      "panel_id": "1",
      "panel_type": "Graphs",
      "bbox": {
        "x0": 92.0,
        "y0": 48.0,
        "x1": 629.0,
        "y1": 430.0
      },
      "uploaded_date": "2025-01-15T10:30:00Z"
    },
    ...
  ]
}
```

#### List Panels from Image

```http
GET /images/{image_id}/panels
Authorization: Bearer <access_token>
```

Returns all panels extracted from a source image, including metadata and bounding boxes.

## Panel Extraction Architecture

### Data Flow

1. User calls `POST /images/extract-panels` with list of image IDs
2. API validates images and queues async Celery task (returns `202 Accepted` with task_id)
3. Celery worker executes `extract_panels_from_images()` task:
   - Retrieves images from MongoDB
   - Calls Docker panel-extractor container
   - Parses PANELS.csv output
   - Maps FIGNAME → source_image_id via database lookup
   - Creates MongoDB document for each extracted panel
   - Saves panel images to `workspace/{user_id}/images/panels/`
4. User polls `GET /images/extract-panels/status/{task_id}` to check progress
5. When completed, panel documents are returned with full metadata

### PANELS.csv Format

The panel-extractor Docker container outputs a CSV file with the following format:

```csv
FIGNAME,ID,LABEL,X0,Y0,X1,Y1
fig1,1,Graphs,92.0,48.0,629.0,430.0
fig1,2,Graphs,755.0,48.0,1413.0,430.0
```

Where:
- **FIGNAME**: Filename of the source image (without extension)
- **ID**: Panel identifier from the YOLO model
- **LABEL**: Panel type classification (e.g., "Graphs", "Blots", "Charts")
- **X0, Y0, X1, Y1**: Bounding box coordinates (top-left and bottom-right corners)

### Data Model

```python
class Panel(MongoDB Document):
    _id: ObjectId              # Unique panel ID
    user_id: str              # User who owns the image
    filename: str             # Panel image filename
    file_path: str            # Path to panel image file
    file_size: int            # Panel image size in bytes
    source_type: str          # "panel" (indicates extracted panel)
    source_image_id: ObjectId # Link to source image
    panel_id: str             # ID from PANELS.csv
    panel_type: str           # Classification from YOLO model
    bbox: {                   # Bounding box coordinates
        x0: float,
        y0: float,
        x1: float,
        y1: float
    }
    uploaded_date: datetime   # When extracted
    created_at: datetime      # When document created
```

### Implementation Files

- **`app/utils/docker_panel_extractor.py`**: Docker orchestration and PANELS.csv parsing
- **`app/tasks/panel_extraction.py`**: Celery async task with retry logic
- **`app/services/panel_extraction_service.py`**: Business logic and validation
- **`app/routes/images.py`**: API endpoints (3 new endpoints)
- **`app/schemas.py`**: Pydantic request/response schemas
- **`app/config/settings.py`**: Configuration constants

#### Delete Document

```http
DELETE /documents/{document_id}
Authorization: Bearer <access_token>
```

Deletes document and associated extracted images.

### Image Management Endpoints

#### Upload Image

```http
POST /images/upload
Authorization: Bearer <access_token>
Content-Type: multipart/form-data

file: <image file>
document_id: <document_id>
```

#### List Images

```http
GET /images
Authorization: Bearer <access_token>
```

#### Get Extracted Images for Document

```http
GET /documents/{document_id}/images
Authorization: Bearer <access_token>
```

#### Delete Image

```http
DELETE /images/{image_id}
Authorization: Bearer <access_token>
```

## Architecture

### System Overview

The ELIS system uses a distributed architecture with separate services for web APIs, background processing, and data storage:

```
┌─────────────────┐
│   FastAPI       │
│   (Port 8000)   │ ◄────┐
└────────┬────────┘      │
         │               │
         ├─► MongoDB     │ Client
         │   (Main DB)   │ Requests
         │               │
         ├─► Redis ◄─────┼──────┐
         │                      │
         └───► Celery Workers   │
              (Task Processing) │
                                │
         ┌──────────────────────┘
         │
      Workspace
    (Shared Storage)
```

### Component Responsibilities

#### FastAPI API Layer

- Handles HTTP requests and responses
- Performs request validation with Pydantic
- Manages user authentication with JWT tokens
- Routes requests to appropriate handlers
- Submits long-running tasks to Celery

#### Celery Workers

- Process background tasks asynchronously
- Extract images from uploaded PDFs
- Perform heavy computations without blocking API
- Retry failed tasks automatically
- Multiple workers for parallel processing

#### MongoDB Database

- Stores user accounts and authentication data
- Stores document metadata and extraction history
- Maintains extraction status and task information
- Provides persistent data layer

#### Redis Message Broker

- Transfers task messages between API and workers
- Stores task results temporarily
- Manages task queue distribution
- Enables worker communication

### Data Flow - Document Upload

1. User uploads PDF file to FastAPI endpoint
2. FastAPI validates file and saves to `workspace/{user_id}/pdfs/`
3. MongoDB document record created with status `pending`
4. Celery task message sent to Redis
5. Available Celery worker picks up task
6. Worker extracts images from PDF
7. Images saved to `workspace/{user_id}/images/extracted/{doc_id}/`
8. MongoDB document status updated to `completed`
9. API returns document ID (can be checked for status)

### Module Organization

#### Database Layer (`app/db/`)

- MongoDB connection management
- Singleton pattern for connection pooling
- Dynamic connection URL reading for testing
- Collection access methods

#### Security Layer (`app/utils/`)

- Password hashing and verification using bcrypt
- JWT token generation and validation
- OAuth2 scheme implementation
- User authentication dependency

#### Task Processing (`app/celery_config.py` and `app/tasks/`)

- Celery configuration and initialization
- Async task definitions for image extraction
- Redis integration for message brokering
- Retry logic and error handling

#### Routes Layer (`app/routes/`)

- Authentication endpoints (register, login)
- User management endpoints (profile, update, delete)
- Document upload and management endpoints
- Image management endpoints
- Clear separation of concerns

#### Schemas Layer (`app/schemas.py`)

- Pydantic data validation
- Request/response models
- Type hints for IDE support
- Automatic OpenAPI documentation

#### File Storage (`app/utils/file_storage.py`)

- User-isolated directory management
- PDF upload handling
- Image extraction output paths
- File system operations

## Testing

Run the complete test suite with pytest:

```bash
# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v

# Run specific test file
pytest tests/test_document_upload.py -v

# Run with coverage report
pytest tests/ --cov=app --cov-report=html
```

### Test Organization

- **test_user_operations.py**: User registration, login, and profile management
- **test_document_upload.py**: Document upload, image extraction, and file storage
- Tests use separate MongoDB database on port 27018
- Redis connection verified for Celery task testing
- Full integration testing with actual services

### Test Data

Test fixtures automatically:

- Create temporary test database
- Clean up test documents after each test
- Isolate user data per test
- Provide sample PDF and image files

## Environment Configuration

### Required Variables

```env
MONGODB_URL=mongodb://localhost:27017
DATABASE_NAME=elis_system
JWT_SECRET=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0
```

### Docker Environment

When running with Docker Compose, environment variables are automatically configured:

```env
MONGODB_URL=mongodb://mongodb:27017
REDIS_URL=redis://redis:6379/0
```

## Dependencies

### Web Framework

- **fastapi**: Modern async web framework with automatic API documentation
- **uvicorn**: ASGI server for running FastAPI
- **starlette**: Web framework foundation used by FastAPI

### Database

- **pymongo**: Python driver for MongoDB
- **dnspython**: DNS support for MongoDB connection strings

### Async Task Processing

- **celery**: Distributed task queue
- **redis**: Message broker and result backend
- **flower**: Celery monitoring and management interface

### Authentication & Security

- **pyjwt**: JWT token generation and validation
- **passlib**: Password hashing framework
- **bcrypt**: Secure password hashing algorithm
- **cryptography**: Cryptographic recipes and primitives

### Data Validation

- **pydantic**: Data validation and serialization using type hints
- **email-validator**: Email validation for user registration

### Utilities

- **python-dotenv**: Environment variable management
- **python-multipart**: Multipart form data parsing
- **typing-extensions**: Backports of new typing features

### Development & Testing

- **pytest**: Testing framework
- **pytest-asyncio**: Async test support
- **httpx**: HTTP client for API testing

## Key Capabilities Summary

### Document Processing
- Upload PDF documents with storage quota management
- Automatic extraction of images from PDFs (async with Celery)
- Document status tracking and retrieval

### Image Management
- Extracted image storage with user isolation
- Image metadata tracking (source, dimensions, upload date)
- Image retrieval by document or individual ID
- Storage quota enforcement per user

### Panel Extraction (Scientific Image Analysis)
- Extract individual panels/figures from scientific images
- YOLO-based panel detection and classification
- Bounding box coordinates for each panel
- Panel type classification (Graphs, Blots, Charts, etc.)
- Async task processing with status polling

### User Management & Security
- User registration and authentication with JWT
- Password hashing with bcrypt
- Role-based access control
- Storage quota enforcement
- User data isolation

### System Architecture
- **API Layer**: FastAPI with automatic OpenAPI documentation
- **Async Processing**: Celery workers with Redis message broker
- **Database**: MongoDB for flexible document storage
- **Docker Integration**: Container wrappers for PDF and panel extraction
- **Monitoring**: Flower dashboard for task monitoring

## API Quick Reference

All endpoints require JWT authentication (except `/auth/`).

**Complete API documentation**: See **API_REFERENCE.md** for detailed endpoint specifications, examples, and response formats.

Quick endpoint list:

```bash
# Auth
POST /auth/register
POST /auth/login

# Users
GET /users/me
PUT /users/me
DELETE /users/me

# Documents
POST /documents/upload
GET /documents
GET /documents/{doc_id}
DELETE /documents/{doc_id}

# Images
GET /images
GET /images/{image_id}
DELETE /images/{image_id}

# Panel Extraction
POST /images/extract-panels              # Initiate (async, returns task_id)
GET /images/extract-panels/status/{task_id}  # Check status
GET /images/{image_id}/panels            # List panels from image

# Annotations
GET /annotations
POST /annotations
GET /annotations/{anno_id}
PUT /annotations/{anno_id}
DELETE /annotations/{anno_id}
```

For complete request/response examples, curl commands, and error handling, see **API_REFERENCE.md**.

## Troubleshooting

### MongoDB Connection Failed

- Ensure MongoDB is running: `mongod` or `mongo` service
- Check MONGODB_URL in .env file
- Verify firewall allows connection to MongoDB port (27017)

### JWT Token Errors

- Ensure JWT_SECRET is set in .env
- Check token hasn't expired
- Verify token format: `Authorization: Bearer <token>`

### Duplicate Key Error

- Username or email already exists in database
- Clear database: `db.users.deleteMany({})`
- Or use different username/email

### Password Verification Failed

- Ensure password meets minimum length (4 characters)
- Check password matches stored hash
- Verify bcrypt library is properly installed

### Running Tests

```bash
# All tests
pytest tests/ -v

# Panel extraction tests
pytest tests/test_panel_extraction.py tests/test_panel_extraction_docker.py -v

# With coverage
pytest tests/ --cov=app --cov-report=html
```

### Docker Services

Key images:
- `panel-extractor:latest` - Panel extraction (YOLO-based)
- `pdf-image-extraction:latest` - PDF to images
- `panel-watermark-removal:latest` - Watermark removal
- MongoDB, Redis, FastAPI (in docker-compose.yml)

