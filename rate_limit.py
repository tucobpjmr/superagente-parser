"""
Distributed rate limiter backed by Upstash Redis REST API.

Uses a fixed-window counter (one bucket per minute, per IP). Atomic via
Upstash pipeline (INCR + EXPIRE). No-op when UPSTASH_REDIS_REST_URL is unset,
so local dev / tests don't require Redis.
"""

import os
import time
import logging
from typing import Optional

import httpx
from fastapi import HTTPException, Request

log = logging.getLogger("parser.ratelimit")

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "10"))
RATE_LIMIT_TIMEOUT_S = float(os.getenv("RATE_LIMIT_TIMEOUT_S", "1.0"))

_enabled = bool(UPSTASH_URL and UPSTASH_TOKEN)
_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=UPSTASH_URL,
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=RATE_LIMIT_TIMEOUT_S,
        )
    return _client


def _client_ip(request: Request) -> str:
    # Trust X-Forwarded-For only when behind Railway/proxy (first hop is client).
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def check_rate_limit(request: Request) -> None:
    """FastAPI dependency. Raises 429 when the per-IP minute budget is exhausted."""
    if not _enabled:
        return

    ip = _client_ip(request)
    bucket = int(time.time() // 60)
    key = f"ratelimit:parse:{ip}:{bucket}"

    try:
        # Upstash pipeline: atomic INCR + EXPIRE in one round-trip.
        # https://upstash.com/docs/redis/features/restapi#pipeline
        resp = await _get_client().post(
            "/pipeline",
            json=[["INCR", key], ["EXPIRE", key, 90]],
        )
        resp.raise_for_status()
        results = resp.json()
        count = int(results[0]["result"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
        # Fail open: Redis blip must not break uploads.
        log.warning("rate limit check failed (fail-open): %s", e)
        return

    if count > RATE_LIMIT_PER_MIN:
        retry_after = 60 - (int(time.time()) % 60)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT_PER_MIN} requests/min per IP",
            headers={"Retry-After": str(retry_after)},
        )
