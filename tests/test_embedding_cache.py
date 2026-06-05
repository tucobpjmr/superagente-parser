"""
Tests per cache embedding (Upstash REST + SHA-256).
"""

import hashlib
import importlib
import json
import os
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _reload(**env):
    for k in (
        "UPSTASH_REDIS_REST_URL",
        "UPSTASH_REDIS_REST_TOKEN",
        "EMBEDDING_CACHE_TTL_S",
    ):
        os.environ.pop(k, None)
    for k, v in env.items():
        os.environ[k] = v

    import embedding_cache
    importlib.reload(embedding_cache)
    return embedding_cache


def _resp(results):
    r = MagicMock()
    r.raise_for_status = MagicMock()
    r.json.return_value = [{"result": x} for x in results]
    return r


class TestKey:
    def test_sha256_stable(self):
        ec = _reload()
        k1 = ec._key("m", "ciao")
        k2 = ec._key("m", "ciao")
        assert k1 == k2
        assert hashlib.sha256(b"ciao").hexdigest() in k1


class TestDisabled:
    @pytest.mark.asyncio
    async def test_get_returns_all_miss_when_disabled(self):
        ec = _reload()
        result = await ec.get_many("m", ["a", "b", "c"])
        assert result == [None, None, None]

    @pytest.mark.asyncio
    async def test_set_noop_when_disabled(self):
        ec = _reload()
        await ec.set_many("m", [("a", [0.1])])

    @pytest.mark.asyncio
    async def test_empty_texts(self):
        ec = _reload(UPSTASH_REDIS_REST_URL="https://x", UPSTASH_REDIS_REST_TOKEN="t")
        assert await ec.get_many("m", []) == []


class TestEnabled:
    @pytest.mark.asyncio
    async def test_get_many_mixed_hits(self, monkeypatch):
        ec = _reload(UPSTASH_REDIS_REST_URL="https://x", UPSTASH_REDIS_REST_TOKEN="t")
        cached_emb = [0.1, 0.2, 0.3]
        client = MagicMock()
        client.post = AsyncMock(
            return_value=_resp([json.dumps(cached_emb), None, json.dumps(cached_emb)])
        )
        monkeypatch.setattr(ec, "_get_client", lambda: client)

        out = await ec.get_many("m", ["x", "y", "z"])
        assert out[0] == cached_emb
        assert out[1] is None
        assert out[2] == cached_emb

    @pytest.mark.asyncio
    async def test_get_many_redis_error_fails_open(self, monkeypatch):
        ec = _reload(UPSTASH_REDIS_REST_URL="https://x", UPSTASH_REDIS_REST_TOKEN="t")
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.ConnectTimeout("boom"))
        monkeypatch.setattr(ec, "_get_client", lambda: client)

        out = await ec.get_many("m", ["a", "b"])
        assert out == [None, None]

    @pytest.mark.asyncio
    async def test_get_many_invalid_json_returns_none(self, monkeypatch):
        ec = _reload(UPSTASH_REDIS_REST_URL="https://x", UPSTASH_REDIS_REST_TOKEN="t")
        client = MagicMock()
        client.post = AsyncMock(return_value=_resp(["not-json"]))
        monkeypatch.setattr(ec, "_get_client", lambda: client)

        assert await ec.get_many("m", ["x"]) == [None]

    @pytest.mark.asyncio
    async def test_set_many_pipeline_format(self, monkeypatch):
        ec = _reload(
            UPSTASH_REDIS_REST_URL="https://x",
            UPSTASH_REDIS_REST_TOKEN="t",
            EMBEDDING_CACHE_TTL_S="60",
        )
        captured = {}

        async def fake_post(path, json):
            captured["json"] = json
            return _resp([1, 1])

        client = MagicMock()
        client.post = AsyncMock(side_effect=fake_post)
        monkeypatch.setattr(ec, "_get_client", lambda: client)

        await ec.set_many("m", [("a", [0.1]), ("b", [0.2])])
        assert len(captured["json"]) == 2
        cmd = captured["json"][0]
        assert cmd[0] == "SET"
        assert cmd[3] == "EX"
        assert cmd[4] == 60

    @pytest.mark.asyncio
    async def test_set_many_error_swallowed(self, monkeypatch):
        ec = _reload(UPSTASH_REDIS_REST_URL="https://x", UPSTASH_REDIS_REST_TOKEN="t")
        client = MagicMock()
        client.post = AsyncMock(side_effect=httpx.HTTPError("nope"))
        monkeypatch.setattr(ec, "_get_client", lambda: client)

        await ec.set_many("m", [("a", [0.1])])
