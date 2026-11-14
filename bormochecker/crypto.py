"""Lightweight HMAC token signing and in-memory sliding-window rate limiting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key: bytes, payload: dict) -> str:
    """Sign a JSON-serializable dict payload with HMAC-SHA256."""
    body = _b64u(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    mac = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64u(mac)}"


def unsign(key: bytes, token: str) -> dict | None:
    """Verify and decode an HMAC-SHA256 signed token. Returns None on any failure."""
    try:
        body, mac = token.split(".")
        want = hmac.new(key, body.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64u(mac)):
            return None
        payload = json.loads(_unb64u(body))
    except (ValueError, AttributeError):
        return None
    return payload if isinstance(payload, dict) else None


class RateLimiter:
    """In-memory sliding-window rate limiter keyed by user/session identifier."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, key: str, window: float) -> deque[float]:
        q = self._hits[key]
        cutoff = self._clock() - window
        while q and q[0] <= cutoff:
            q.popleft()
        if not q:
            self._hits.pop(key, None)
        return q

    def blocked(self, key: str, limit: int, window: float) -> bool:
        with self._lock:
            return len(self._trim(key, window)) >= limit

    def hit(self, key: str, window: float) -> None:
        with self._lock:
            self._trim(key, window)
            self._hits[key].append(self._clock())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
