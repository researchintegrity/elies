"""
ELIES Scientific Image Analysis System

The FastAPI application lives in ``app.main`` (``uvicorn app.main:app``) and the
Celery application in ``app.celery_config``. This package module deliberately
imports neither, so Celery workers do not load the web application.
"""

__version__ = "0.1.0"
__author__ = "João Phillipe Cardenuto"
__title__ = "ELIES Scientific Image Analysis System"
__description__ = "A back-end service for scientific image analysis."
