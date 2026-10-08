"""
Sliding-window rate limiting for authentication endpoints.

Events are kept in Redis (one sorted set per key), so every API process
shares the same counts. While Redis is unreachable each process counts on
its own in memory: limits stay enforced, only per process.
"""
import logging
import math
import threading
import time
import uuid
from collections import deque
from typing import Deque, Dict, Optional

import redis

from app.utils.redis_client import get_redis, mark_unavailable

logger = logging.getLogger(__name__)

KEY_PREFIX = "elies:ratelimit:"


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per key within ``window_seconds``."""

    def __init__(self, name: str, limit: int, window_seconds: int, clock=time.monotonic):
        self.name = name
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    # -- shared (Redis) ---------------------------------------------------

    def _redis_key(self, key: str) -> str:
        return f"{KEY_PREFIX}{self.name}:{key}"

    def _redis_retry_after(self, client, key: str) -> Optional[int]:
        now = time.time()
        pipe = client.pipeline()
        pipe.zremrangebyscore(self._redis_key(key), 0, now - self.window)
        pipe.zcard(self._redis_key(key))
        pipe.zrange(self._redis_key(key), 0, 0, withscores=True)
        _, count, oldest = pipe.execute()
        if count < self.limit:
            return None
        return max(1, math.ceil(oldest[0][1] + self.window - now))

    def _redis_hit(self, client, key: str) -> None:
        now = time.time()
        pipe = client.pipeline()
        pipe.zadd(self._redis_key(key), {f"{now}:{uuid.uuid4().hex[:8]}": now})
        pipe.zremrangebyscore(self._redis_key(key), 0, now - self.window)
        pipe.expire(self._redis_key(key), self.window)
        pipe.execute()

    # -- per process (fallback) -------------------------------------------

    def _prune(self, key: str, now: float) -> Deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def _local_retry_after(self, key: str) -> Optional[int]:
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if len(events) < self.limit:
                return None
            return max(1, math.ceil(events[0] + self.window - now))

    def _local_hit(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(key, now)
            self._events.setdefault(key, deque()).append(now)

    # -- public API -------------------------------------------------------

    def retry_after(self, key: str) -> Optional[int]:
        """Seconds until ``key`` may act again, or None if it is not limited."""
        client = get_redis()
        if client is not None:
            try:
                return self._redis_retry_after(client, key)
            except redis.RedisError as e:
                mark_unavailable(e, "rate limiting")
        return self._local_retry_after(key)

    def hit(self, key: str) -> None:
        """Record one event for ``key``."""
        client = get_redis()
        if client is not None:
            try:
                self._redis_hit(client, key)
                return
            except redis.RedisError as e:
                mark_unavailable(e, "rate limiting")
        self._local_hit(key)

    def reset(self, key: str) -> None:
        """Forget the events of ``key`` (e.g. after a successful login)."""
        with self._lock:
            self._events.pop(key, None)
        client = get_redis()
        if client is not None:
            try:
                client.delete(self._redis_key(key))
            except redis.RedisError as e:
                mark_unavailable(e, "rate limiting")

    def clear(self) -> None:
        """Forget every key of this limiter (tests and administration)."""
        with self._lock:
            self._events.clear()
        client = get_redis()
        if client is not None:
            try:
                keys = list(client.scan_iter(match=f"{KEY_PREFIX}{self.name}:*", count=500))
                if keys:
                    client.delete(*keys)
            except redis.RedisError as e:
                mark_unavailable(e, "rate limiting")
