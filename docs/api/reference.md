# API Reference

The ELIES system provides a RESTful API documented using OpenAPI (Swagger).

## Interactive Documentation

When running the system locally, you can access the interactive API documentation at:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

## Key Endpoints

### Authentication
- `POST /auth/token`: Login and retrieve access token.
- `POST /users/`: Register a new user.

### Documents
- `POST /documents/upload`: Upload a PDF file.
- `GET /documents/`: List user documents.
- `GET /documents/{id}`: Get document details.

### Images
- `GET /images/`: List extracted images.
- `GET /images/{id}/analysis`: Get analysis results for an image.
