"""
Cache embeddings su Upstash Redis REST, indicizzati per SHA-256 del testo.

Chiave: `emb:{model}:{sha256(text)}`. Hit-rate dipende dalla ripetizione di
chunk identici tra documenti (es. boilerplate normativo, intestazioni ricorrenti).

Disattivato se UPSTASH_REDIS_REST_URL non è impostato (no-op, ritorna tutti miss).
Fail-open su errori Redis: un blip non blocca l'embedding.
"""

import hashlib
import json
import logging
import os
from typing import List, Optional, Sequence, Tuple

import httpx

log = logging.getLogger("parser.embcache")

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL", "").rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN", "")
TTL_S = int(os.getenv("EMBEDDING_CACHE_TTL_S", str(30 * 24 * 3600)))  # 30 giorni
TIMEOUT_S = float(os.getenv("EMBEDDING_CACHE_TIMEOUT_S", "2.0"))

_enabled = bool(UPSTASH_URL and UPSTASH_TOKEN)
_client: Optional[httpx.AsyncClient] = None


def is_enabled() -> bool:
    return _enabled


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=UPSTASH_URL,
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=TIMEOUT_S,
        )
    return _client


def _key(model: str, text: str) -> str:
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"emb:{model}:{h}"


async def get_many(model: str, texts: Sequence[str]) -> List[Optional[List[float]]]:
    """Bulk lookup. Ritorna lista con None per ogni miss, embedding per ogni hit."""
    if not _enabled or not texts:
        return [None] * len(texts)

    keys = [_key(model, t) for t in texts]
    try:
        resp = await _get_client().post(
            "/pipeline",
            json=[["GET", k] for k in keys],
        )
        resp.raise_for_status()
        results = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("cache GET failed (fail-open): %s", e)
        return [None] * len(texts)

    out: List[Optional[List[float]]] = []
    for r in results:
        raw = r.get("result") if isinstance(r, dict) else None
        if raw is None:
            out.append(None)
            continue
        try:
            out.append(json.loads(raw))
        except (TypeError, ValueError):
            out.append(None)
    return out


async def set_many(model: str, items: Sequence[Tuple[str, List[float]]]) -> None:
    """Bulk write con TTL. Errori loggati ma non sollevati."""
    if not _enabled or not items:
        return

    pipeline = [
        ["SET", _key(model, text), json.dumps(emb), "EX", TTL_S]
        for text, emb in items
    ]
    try:
        resp = await _get_client().post("/pipeline", json=pipeline)
        resp.raise_for_status()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("cache SET failed: %s", e)
