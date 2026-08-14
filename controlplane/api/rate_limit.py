"""In-memory sliding-window rate limiter (docs/PLATFORM_SPEC.md §7.6).

Single-process control plane in v1; horizontal scaling is out of scope.
"""

import threading
import time
from collections import deque


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, max_events: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] <= now - window_seconds:
                bucket.popleft()
            if len(bucket) >= max_events:
                return False
            bucket.append(now)
            return True


_limiter = RateLimiter()


def check_rate_limit(key: str, max_events: int, window_seconds: int) -> bool:
    return _limiter.allow(key, max_events, window_seconds)
