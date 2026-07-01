"""Lightweight in-memory rate limiting for sensitive HTTP endpoints."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

from flask import abort, request

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


def _client_ip() -> str:
    forwarded = (request.headers.get('X-Forwarded-For') or '').strip()
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.remote_addr or 'unknown'


def rate_limit(key: str, *, limit: int, window_seconds: int) -> None:
    """Abort with HTTP 429 when *key* exceeds *limit* events in *window_seconds*."""
    now = time.time()
    with _lock:
        bucket = _buckets[key]
        bucket[:] = [stamp for stamp in bucket if now - stamp < window_seconds]
        if len(bucket) >= limit:
            abort(429)
        bucket.append(now)


def rate_limit_client(route_key: str, *, limit: int, window_seconds: int) -> None:
    """Rate limit by client IP for a named route."""
    rate_limit(f'{route_key}:{_client_ip()}', limit=limit, window_seconds=window_seconds)


def reset_rate_limits_for_tests() -> None:
    """Clear counters between tests."""
    with _lock:
        _buckets.clear()
