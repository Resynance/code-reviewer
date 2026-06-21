"""
rate_limit.py — per-user sliding-window rate limiter for expensive endpoints.

Keyed by user email (from JWT) or "anon" when auth is disabled.
Configured via env vars:
  RATE_LIMIT_REQUESTS  — max requests per window (default 20, 0 = disabled)
  RATE_LIMIT_WINDOW    — window size in seconds (default 3600)

In-memory: protects against bursts within a single process. On Vercel each
function instance has its own window, so the effective limit is
RATE_LIMIT_REQUESTS per instance rather than globally — acceptable for
preventing accidental hammering; a persistent backend (e.g. Supabase counter)
would be needed for strict cross-instance enforcement.
"""

import os
import time
import threading
from collections import defaultdict, deque

_lock = threading.Lock()
_windows: dict = defaultdict(deque)


def _limit() -> int:
    return int(os.getenv("RATE_LIMIT_REQUESTS", "20"))


def _window() -> int:
    return int(os.getenv("RATE_LIMIT_WINDOW", "3600"))


def check(key: str) -> tuple:
    """Sliding-window check. Returns (allowed: bool, retry_after: int).

    Thread-safe. Timestamps older than the window are evicted on each call so
    the deque stays bounded to at most RATE_LIMIT_REQUESTS entries.
    """
    limit = _limit()
    if limit == 0:
        return True, 0

    now = time.monotonic()
    window = _window()
    cutoff = now - window

    with _lock:
        dq = _windows[key]
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = int(dq[0] - cutoff) + 1
            return False, retry_after
        dq.append(now)
        return True, 0
