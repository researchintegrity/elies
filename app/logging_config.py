"""
Logging setup shared by the API and the Celery workers.
"""
import logging

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """
    Configure the root logger once, at the level from LOG_LEVEL.

    Without this, application ``logger.info`` calls were dropped by the default
    WARNING root level while uvicorn's own loggers still printed.
    """
    root = logging.getLogger()
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
    root.setLevel(numeric_level)
