"""
Generazione embedding via OpenAI text-embedding-3-small.
Batch fino a 100 input per chiamata (limite prudenziale, OpenAI ne accetta 2048).
Retry con backoff esponenziale per errori transitori.
Cache opzionale Upstash Redis per SHA-256 del testo (vedi embedding_cache.py).
"""

import logging
import os
from typing import List

from openai import AsyncOpenAI
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import openai

import embedding_cache

log = logging.getLogger("parser.embeddings")

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
BATCH_SIZE = 100

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY non impostata")
        _client = AsyncOpenAI(api_key=api_key)
    return _client


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(4),
    retry=retry_if_exception_type(
        (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)
    ),
    reraise=True,
)
async def _embed_batch(texts: List[str]) -> List[List[float]]:
    client = get_client()
    resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


async def generate_embeddings_batch(texts: List[str]) -> List[List[float]]:
    """
    Genera embedding per lista di testi, in batch da BATCH_SIZE.
    Cache lookup per SHA-256 (se Upstash configurato): solo i miss vanno a OpenAI.
    Ritorna lista di vettori nello stesso ordine dell'input.
    """
    if not texts:
        return []

    cached = await embedding_cache.get_many(EMBEDDING_MODEL, texts)
    miss_idx = [i for i, c in enumerate(cached) if c is None]

    if embedding_cache.is_enabled():
        log.info(
            "embedding cache: %d/%d hit (%d miss → OpenAI)",
            len(texts) - len(miss_idx),
            len(texts),
            len(miss_idx),
        )

    new_embeddings: List[List[float]] = []
    if miss_idx:
        miss_texts = [texts[i] for i in miss_idx]
        for i in range(0, len(miss_texts), BATCH_SIZE):
            batch = miss_texts[i : i + BATCH_SIZE]
            new_embeddings.extend(await _embed_batch(batch))

        await embedding_cache.set_many(
            EMBEDDING_MODEL, list(zip(miss_texts, new_embeddings))
        )

    results: List[List[float]] = [c if c is not None else [] for c in cached]
    for idx, emb in zip(miss_idx, new_embeddings):
        results[idx] = emb
    return results
