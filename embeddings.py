"""
Generazione embedding via OpenAI text-embedding-3-small.
Batch fino a 100 input per chiamata (limite prudenziale, OpenAI ne accetta 2048).
Retry con backoff esponenziale per errori transitori.
"""

import asyncio
import os
from typing import List
from openai import AsyncOpenAI
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
import openai


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
    Genera embedding per lista di testi, in batch da BATCH_SIZE eseguiti in parallelo.
    Ritorna lista di vettori nello stesso ordine dell'input.
    """
    if not texts:
        return []

    batches = [texts[i : i + BATCH_SIZE] for i in range(0, len(texts), BATCH_SIZE)]
    results_nested: List[List[List[float]]] = await asyncio.gather(
        *[_embed_batch(b) for b in batches]
    )
    return [emb for batch_result in results_nested for emb in batch_result]
