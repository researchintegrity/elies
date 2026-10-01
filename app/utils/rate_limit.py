"""
In-process sliding-window rate limiting for authentication endpoints.

State lives in the API process, which matches the single-process API in the
compose files. With several API processes each one counts separately, so the
effective limit is multiplied by the number of processes.
"""
import math
import threading
import time
from collections import deque
from typing import Deque, Dict, Optional


class SlidingWindowLimiter:
    """Allow at most ``limit`` events per key within ``window_seconds``."""

    def __init__(self, limit: int, window_seconds: int, clock=time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> Deque[float]:
        events = self._events.get(key)
        if events is None:
            return deque()
        while events and events[0] <= now - self.window:
            events.popleft()
        if not events:
            del self._events[key]
        return events

    def retry_after(self, key: str) -> Optional[int]:
        """Seconds until ``key`` may act again, or None if it is not limited."""
        with self._lock:
            now = self._clock()
            events = self._prune(key, now)
            if len(events) < self.limit:
                return None
            return max(1, math.ceil(events[0] + self.window - now))

    def hit(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            self._prune(key, now)
            self._events.setdefault(key, deque()).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
