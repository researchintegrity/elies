"""
ELIES Scientific Image Analysis System

Usage:
    from app.main import app
    # or
    python -m app
"""

__version__ = "0.0.1"
__author__ = "João Phillipe Cardenuto"
__title__ = "ELIES Scientific Image Analysis System"
__description__ = "A backed-end service for Image Analysis."

# Package exports for convenient imports
from app.main import app

__all__ = ["app"]
