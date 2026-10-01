"""
Shared Redis connection for API-side features that must work across API
processes (job events, rate limits).

After a connection failure the client is not used for RETRY_AFTER seconds,
so callers fall back to their in-process behaviour without waiting on
timeouts at every request.
"""
import logging
import time
from typing import Optional

import redis

from app.config.settings import JOB_EVENTS_REDIS_URL

logger = logging.getLogger(__name__)

RETRY_AFTER = 30  # seconds before trying Redis again after a failure

_client: Optional[redis.Redis] = None
_unavailable_until = 0.0


def get_redis() -> Optional[redis.Redis]:
    """The shared client, or None while Redis is considered unavailable."""
    global _client
    if time.monotonic() < _unavailable_until:
        return None
    if _client is None:
        _client = redis.Redis.from_url(JOB_EVENTS_REDIS_URL, socket_connect_timeout=1, socket_timeout=2)
    return _client


def mark_unavailable(error: Exception, purpose: str) -> None:
    """Stop using Redis for a while after ``error``."""
    global _unavailable_until
    logger.warning("Redis unavailable for %s (%s); using the in-process fallback for %ss",
                   purpose, error, RETRY_AFTER)
    _unavailable_until = time.monotonic() + RETRY_AFTER
