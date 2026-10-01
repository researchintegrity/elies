"""
ELIES Scientific Image Analysis System
"""
import logging

from bson.errors import InvalidId
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.settings import ALLOWED_ORIGINS, LOG_LEVEL
from app.db.mongodb import db_connection
from app.exceptions import ELIESException
from app.logging_config import configure_logging
from app.utils.security import RedactTokenFilter
from app.routes import (
    admin,
    analyses,
    api,
    auth,
    cbir,
    documents,
    dual_annotations,
    images,
    jobs,
    provenance,
    relationships,
    single_annotations,
    users,
)

configure_logging(LOG_LEVEL)
logger = logging.getLogger(__name__)

# Media URLs carry ?token=...; keep bearer tokens out of the access log
logging.getLogger("uvicorn.access").addFilter(RedactTokenFilter())

# Create FastAPI app
app = FastAPI(
    title="ELIES Scientific Image Analysis System",
    description="A backed-end service for Image Analysis",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS: only the configured frontend origins (ALLOWED_ORIGINS). The frontend
# authenticates with a bearer header, so credentialed CORS is not needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
    max_age=600,
)

# Include routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(documents.router)
app.include_router(images.router)
app.include_router(single_annotations.router)
app.include_router(dual_annotations.router)
# Legacy annotations router removed per user request
app.include_router(analyses.router)
app.include_router(cbir.router)
app.include_router(provenance.router)
app.include_router(admin.router)
app.include_router(relationships.router)
app.include_router(jobs.router)
app.include_router(api.router)


# ============================================================================
# EXCEPTION HANDLERS
# ============================================================================
@app.exception_handler(ELIESException)
async def elies_exception_handler(request: Request, exc: ELIESException) -> JSONResponse:
    """
    Handle custom ELIES exceptions and convert to JSON responses.

    Services raise domain exceptions (ValidationError, ResourceNotFoundError,
    etc.) which are converted to the matching HTTP status here.
    """
    logger.warning(
        "ELIES exception: %s (status=%d, path=%s)",
        exc.message,
        exc.status_code,
        request.url.path
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message}
    )


@app.exception_handler(InvalidId)
async def invalid_id_handler(request: Request, exc: InvalidId) -> JSONResponse:
    """A malformed ObjectId in a path, query or body is a client error."""
    return JSONResponse(status_code=400, content={"detail": "Invalid ID format"})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected errors in full, but never return internals to the client."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ============================================================================
# LIFECYCLE EVENTS
# ============================================================================
@app.on_event("startup")
async def startup_event() -> None:
    """Initialize database connection on startup."""
    try:
        db_connection.connect()
    except Exception as e:
        logger.error("Failed to connect to MongoDB: %s", str(e))


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connection on shutdown"""
    db_connection.disconnect()


# ============================================================================
# ROOT & HEALTH ENDPOINTS
# ============================================================================
@app.get("/", tags=["General"])
def root() -> dict:
    """
    Root endpoint - API information
    
    Provides information about available endpoints and API version
    """
    return {
        "message": "Welcome to ELIES User Management System",
        "version": "1.0.0",
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc"
        },
        "endpoints": {
            "register": "POST /auth/register",
            "login": "POST /auth/login",
            "profile": "GET /users/me",
            "health": "GET /health"
        }
    }


@app.get("/health", tags=["General"])
def health_check() -> dict:
    """
    Health check endpoint
    
    Verifies MongoDB connection and API status
    """
    try:
        db = db_connection.get_database()
        db.client.admin.command('ping')
        
        return {
            "status": "healthy",
            "database": "connected",
            "version": "0.0.1"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e),
            "version": "0.0.1"
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
