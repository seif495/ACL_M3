"""Request guards for the public API: admin-only endpoints and per-client rate limits."""
import hmac
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Iterable, Optional, Tuple

from fastapi import Header, HTTPException, Request


def require_admin(x_admin_token: Optional[str] = Header(default=None)) -> None:
    """FastAPI dependency for endpoints that change or reload server state.

    The endpoint is hidden (404) unless ADMIN_TOKEN is set, and callers must send the token in
    the X-Admin-Token header. The comparison is constant-time.
    """
    expected = os.getenv("ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=403, detail="Forbidden")


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    The backend is only reachable through Caddy, which sets X-Forwarded-For to the real client
    address. Take the right-most entry (the one our proxy added), so a client can't pick its own
    key by sending a forged header.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Sliding-window limits per key, e.g. [(10, 60), (100, 3600)] = 10/min and 100/hour.

    In-memory and per-process, which matches the single-worker backend on the VM.
    """

    def __init__(self, limits: Iterable[Tuple[int, float]], clock=time.monotonic):
        self.limits = sorted(limits, key=lambda lw: lw[1])
        self.clock = clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> Optional[float]:
        """Record a hit and return None, or return the seconds to wait if a limit is exceeded."""
        now = self.clock()
        longest = self.limits[-1][1] if self.limits else 0
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= longest:
                hits.popleft()
            for limit, window in self.limits:
                in_window = [t for t in hits if now - t < window]
                if len(in_window) >= limit:
                    return max(0.0, window - (now - in_window[0]))
            hits.append(now)
            if len(self._hits) > 10_000:  # drop idle keys so memory stays bounded
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= longest]:
                    del self._hits[k]
            return None

    def remaining(self, key: str) -> Optional[Tuple[int, float]]:
        """(requests left, window seconds) for the tightest limit, without recording a hit;
        None when there are no limits."""
        if not self.limits:
            return None
        now = self.clock()
        with self._lock:
            hits = list(self._hits.get(key, ()))
        best: Optional[Tuple[int, float]] = None
        for limit, window in self.limits:
            left = max(0, limit - sum(1 for t in hits if now - t < window))
            if best is None or left < best[0]:
                best = (left, window)
        return best

    def dependency(self, request: Request) -> None:
        """FastAPI dependency: 429 with Retry-After when the caller is over a limit."""
        wait = self.check(client_key(request))
        if wait is not None:
            raise HTTPException(
                status_code=429,
                detail="Too many requests, please slow down.",
                headers={"Retry-After": str(int(wait) + 1)},
            )


def limits_from_env(name: str, default: str) -> list:
    """Parse "10/60,100/3600" (count/seconds pairs) from an environment variable."""
    raw = os.getenv(name, default)
    limits = []
    for part in raw.split(","):
        count, _, window = part.strip().partition("/")
        if count and window:
            limits.append((int(count), float(window)))
    return limits
