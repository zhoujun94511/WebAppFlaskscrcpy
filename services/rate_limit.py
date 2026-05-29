"""In-process rate limiting / brute-force lockout.

The app runs single-process (Flask-SocketIO threading mode), so an in-memory
store guarded by a lock is sufficient — no Redis/external dependency. If this
ever scales to multiple workers, swap the backing store for a shared one.

Two reusable pieces:

  * :class:`RateLimiter` — a generic sliding-window counter keyed by an
    arbitrary string. ``record()`` a hit, ``retry_after()`` to see if the key
    is currently locked, ``reset()`` to clear (e.g. on a successful login).
    The login route uses it to count FAILED attempts per (ip, username) and
    lock that pair after too many — classic anti-brute-force.

  * :func:`rate_limit` — a Flask route decorator for plain per-client request
    throttling (e.g. to cap an endpoint at N requests / window). Reusable on
    any blueprint route.

IP note: we key off ``request.remote_addr`` only. ``X-Forwarded-For`` is NOT
trusted (a client can forge it); put a real reverse proxy in front and
populate remote_addr via ProxyFix if you deploy behind one.
"""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Callable

from flask import jsonify, request


def client_ip() -> str:
    return request.remote_addr or "unknown"


class RateLimiter:
    """Sliding-window hit counter with a lockout once the limit is hit.

    ``max_attempts`` hits within ``window_seconds`` trips a lock that lasts
    ``block_seconds``. All durations use a monotonic clock so wall-clock
    changes (NTP, DST) can't shorten or extend a lock.
    """

    def __init__(self, max_attempts: int, window_seconds: int, block_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self.block = block_seconds
        self._hits: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> int:
        """Seconds until ``key`` is allowed again, or 0 if not currently locked."""
        now = time.monotonic()
        with self._lock:
            until = self._blocked_until.get(key, 0.0)
            if until > now:
                return int(until - now) + 1
            if until:  # lock expired — clean it up
                self._blocked_until.pop(key, None)
            return 0

    def record(self, key: str) -> int:
        """Record one hit (e.g. a failed login). Returns the lock duration in
        seconds if this hit just tripped the lock, else 0."""
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            hits.append(now)
            if len(hits) >= self.max_attempts:
                self._blocked_until[key] = now + self.block
                self._hits.pop(key, None)  # window consumed by the lock
                return self.block
            self._hits[key] = hits
            return 0

    def reset(self, key: str) -> None:
        """Clear all state for ``key`` (call on a successful attempt)."""
        with self._lock:
            self._hits.pop(key, None)
            self._blocked_until.pop(key, None)

    def clear_all(self) -> None:
        """Drop every key — mainly for test isolation."""
        with self._lock:
            self._hits.clear()
            self._blocked_until.clear()


def rate_limit(
    max_requests: int,
    per_seconds: int,
    *,
    key_func: Callable[[], str] = client_ip,
    message: str = "请求过于频繁，请稍后再试",
):
    """Decorator: throttle a route to ``max_requests`` per ``per_seconds`` per
    key (client IP by default). Returns HTTP 429 with ``Retry-After`` when
    exceeded. Each decorated route gets its own independent limiter."""
    limiter = RateLimiter(max_requests, per_seconds, per_seconds)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = key_func()
            retry = limiter.retry_after(key)
            if retry > 0:
                resp = jsonify({"status": "failed", "error": message})
                resp.headers["Retry-After"] = str(retry)
                return resp, 429
            limiter.record(key)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
