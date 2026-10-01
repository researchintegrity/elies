"""
ELIES Scientific Image Analysis System
"""
import logging
from contextlib import asynccontextmanager

from bson.errors import InvalidId
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config.settings import ALLOWED_ORIGINS, LOG_FORMAT, LOG_LEVEL
from app.db.mongodb import db_connection
from app.exceptions import ELIESException
from app.logging_config import configure_logging
from app.request_context import REQUEST_ID_HEADER, RequestIdMiddleware
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

configure_logging(LOG_LEVEL, LOG_FORMAT)
logger = logging.getLogger(__name__)

# Media URLs carry ?token=...; keep bearer tokens out of the access log
logging.getLogger("uvicorn.access").addFilter(RedactTokenFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect to MongoDB (creating indexes) before serving; fail fast if it is down."""
    db_connection.connect()
    yield
    db_connection.disconnect()


# Create FastAPI app
app = FastAPI(
    title="ELIES Scientific Image Analysis System",
    description="Back-end API for scientific image integrity analysis",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS: only the configured frontend origins (ALLOWED_ORIGINS). The frontend
# authenticates with a bearer header, so credentialed CORS is not needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", REQUEST_ID_HEADER],
    max_age=600,
)
# Outermost: every response, including CORS and error responses, gets X-Request-ID
app.add_middleware(RequestIdMiddleware)

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
# ROOT & HEALTH ENDPOINTS
# ============================================================================
@app.get("/", tags=["General"])
def root() -> dict:
    """
    Root endpoint - API information
    
    Provides information about available endpoints and API version
    """
    return {
        "message": "Welcome to the ELIES Scientific Image Analysis API",
        "version": __version__,
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


def _database_ready() -> bool:
    try:
        db_connection.get_database().client.admin.command("ping")
        return True
    except Exception as e:
        logger.warning("Readiness: MongoDB unavailable: %s", e)
        return False


def _redis_ready() -> bool:
    from app.services.job_logger import _get_redis

    try:
        client = _get_redis()
        return bool(client and client.ping())
    except Exception as e:
        logger.warning("Readiness: Redis unavailable: %s", e)
        return False


@app.get("/health/live", tags=["General"])
def liveness() -> dict:
    """Liveness: the process is up and serving requests (no dependency checks)."""
    return {"status": "alive", "version": __version__}


@app.get("/health/ready", tags=["General"])
@app.get("/health", tags=["General"])
def readiness() -> JSONResponse:
    """
    Readiness: MongoDB and Redis (task queue, job events) are reachable.

    Answers 503 when one of them is down, so container healthchecks and load
    balancers notice. Failure details go to the log, not to the client.
    """
    checks = {
        "database": "connected" if _database_ready() else "disconnected",
        "redis": "connected" if _redis_ready() else "disconnected",
    }
    healthy = all(value == "connected" for value in checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "healthy" if healthy else "unhealthy", **checks, "version": __version__},
    )
