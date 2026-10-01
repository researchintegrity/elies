"""
Logging setup shared by the API and the Celery workers.

LOG_LEVEL sets the level; LOG_FORMAT selects "text" (default) or "json"
(one JSON object per line, for log collectors). Every record carries the
request ID of the API request or Celery task it belongs to.
"""
import json
import logging

from app.request_context import RequestIdFilter

TEXT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class _EliesHandler(logging.StreamHandler):
    """The root handler installed by configure_logging (so it is installed once)."""


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """
    Configure the root logger once, at the level from LOG_LEVEL.

    Without this, application ``logger.info`` calls were dropped by the default
    WARNING root level while uvicorn's own loggers still printed.
    """
    root = logging.getLogger()
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO
    if not any(isinstance(h, _EliesHandler) for h in root.handlers):
        handler = _EliesHandler()
        handler.addFilter(RequestIdFilter())
        handler.setFormatter(JsonFormatter() if fmt.lower() == "json" else logging.Formatter(TEXT_FORMAT))
        root.addHandler(handler)
    root.setLevel(numeric_level)
