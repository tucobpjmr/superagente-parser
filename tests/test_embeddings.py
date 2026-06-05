"""
Tests per generate_embeddings_batch: integrazione con cache (hit/miss/full).
"""

import importlib
from unittest.mock import AsyncMock, patch

import pytest


def _reload():
    import embeddings
    importlib.reload(embeddings)
    return embeddings


class TestGenerateEmbeddingsBatch:
    @pytest.mark.asyncio
    async def test_empty_input(self):
        emb = _reload()
        assert await emb.generate_embeddings_batch([]) == []

    @pytest.mark.asyncio
    async def test_all_miss_calls_openai_once(self):
        emb = _reload()
        with patch("embeddings.embedding_cache.get_many", new_callable=AsyncMock) as gm, \
             patch("embeddings.embedding_cache.set_many", new_callable=AsyncMock) as sm, \
             patch("embeddings.embedding_cache.is_enabled", return_value=False), \
             patch("embeddings._embed_batch", new_callable=AsyncMock) as eb:
            gm.return_value = [None, None]
            eb.return_value = [[0.1] * 1536, [0.2] * 1536]
            out = await emb.generate_embeddings_batch(["a", "b"])

        assert len(out) == 2
        assert out[0][0] == 0.1
        assert out[1][0] == 0.2
        eb.assert_awaited_once_with(["a", "b"])
        sm.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_hit_skips_openai(self):
        emb = _reload()
        cached = [[0.3] * 1536, [0.4] * 1536]
        with patch("embeddings.embedding_cache.get_many", new_callable=AsyncMock) as gm, \
             patch("embeddings.embedding_cache.set_many", new_callable=AsyncMock) as sm, \
             patch("embeddings.embedding_cache.is_enabled", return_value=True), \
             patch("embeddings._embed_batch", new_callable=AsyncMock) as eb:
            gm.return_value = cached
            out = await emb.generate_embeddings_batch(["a", "b"])

        assert out == cached
        eb.assert_not_awaited()
        sm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_preserves_order(self):
        emb = _reload()
        cached_b = [0.9] * 1536
        with patch("embeddings.embedding_cache.get_many", new_callable=AsyncMock) as gm, \
             patch("embeddings.embedding_cache.set_many", new_callable=AsyncMock) as sm, \
             patch("embeddings.embedding_cache.is_enabled", return_value=True), \
             patch("embeddings._embed_batch", new_callable=AsyncMock) as eb:
            # a=miss, b=hit, c=miss
            gm.return_value = [None, cached_b, None]
            eb.return_value = [[0.1] * 1536, [0.3] * 1536]  # for a, c
            out = await emb.generate_embeddings_batch(["a", "b", "c"])

        assert out[0][0] == 0.1
        assert out[1] == cached_b
        assert out[2][0] == 0.3
        eb.assert_awaited_once_with(["a", "c"])
        # set_many should write only the misses
        set_args = sm.call_args.args
        items = set_args[1]
        assert len(items) == 2
        assert items[0][0] == "a"
        assert items[1][0] == "c"
