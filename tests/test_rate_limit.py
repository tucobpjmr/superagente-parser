"""
Tests for distributed rate limiter (Upstash REST).
"""

import importlib
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException


def _reload(**env):
    for k in (
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "RATE_LIMIT_PER_MIN",
    ):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v

    import rate_limit
    importlib.reload(rate_limit)
    return rate_limit


def _request(ip="1.2.3.4", forwarded=None):
    req = MagicMock()
    req.client = MagicMock(host=ip)
    headers = {}
    if forwarded:
        headers["x-forwarded-for"] = forwarded
    req.headers = headers
    return req


def _pipeline_response(count):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = [{"result": count}, {"result": 1}]
    return resp


class TestDisabled:
    @pytest.mark.asyncio
    async def test_no_env_is_noop(self):
        rl = _reload()
        await rl.check_rate_limit(_request())


class TestEnabled:
    @pytest.mark.asyncio
    async def test_under_limit_allows(self, monkeypatch):
        rl = _reload(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
            RATE_LIMIT_PER_MIN="10",
        )
        client = MagicMock()
        client.post = AsyncMock(return_value=_pipeline_response(5))
        monkeypatch.setattr(rl, "_get_client", lambda: client)

        await rl.check_rate_limit(_request())
        client.post.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_over_limit_429(self, monkeypatch):
        rl = _reload(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
            RATE_LIMIT_PER_MIN="10",
        )
        client = MagicMock()
        client.post = AsyncMock(return_value=_pipeline_response(11))
        monkeypatch.setattr(rl, "_get_client", lambda: client)

        with pytest.raises(HTTPException) as exc:
            await rl.check_rate_limit(_request())
        assert exc.value.status_code == 429
        assert "Retry-After" in exc.value.headers

    @pytest.mark.asyncio
    async def test_redis_failure_fails_open(self, monkeypatch):
        rl = _reload(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
            RATE_LIMIT_PER_MIN="10",
        )
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectTimeout("nope"))
        monkeypatch.setattr(rl, "_get_client", lambda: client)

        await rl.check_rate_limit(_request())

    @pytest.mark.asyncio
    async def test_uses_xff_when_present(self, monkeypatch):
        rl = _reload(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
            RATE_LIMIT_PER_MIN="10",
        )
        captured = {}

        async def fake_post(path, json):
            captured["key"] = json[0][1]
            return _pipeline_response(1)

        client = MagicMock()
        client.post = AsyncMock(side_effect=fake_post)
        monkeypatch.setattr(rl, "_get_client", lambda: client)

        await rl.check_rate_limit(_request(ip="10.0.0.1", forwarded="9.9.9.9, 10.0.0.1"))
        assert "9.9.9.9" in captured["key"]
