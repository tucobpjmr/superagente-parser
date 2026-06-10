"""
D4 — Caching embedding per documenti identici.

Documenti identici producono gli stessi embedding: prima di OCR + chunking +
OpenAI, /parse calcola lo SHA-256 dei byte del file e cerca un documento con
lo stesso `content_hash` su Supabase. In caso di hit, i chunk (con embedding)
vengono riletti da `document_chunks` e restituiti senza alcuna chiamata OpenAI.

Best-effort: qualunque errore (Supabase non configurato, rete, schema) degrada
silenziosamente alla pipeline completa. Il caller resta responsabile di
scrivere `content_hash` su `documenti` all'INSERT (campo già nello schema,
ora restituito in metadata.content_hash).

Nota: la cache è valida finché la pipeline di chunking/embedding non cambia
(CHUNK_SIZE, modello, contextual embedding). Dopo una modifica, svuotare o
re-ingerire i documenti per rigenerare i chunk.
"""

import json
import logging
from typing import Dict, List, Optional

import httpx

# Config Supabase letta dinamicamente dal modulo search: stessa fonte di
# verità (e i test che monkeypatchano search.* coprono anche la cache).
import search as _search


log = logging.getLogger("parser.cache")


def _normalize_embedding(raw) -> Optional[List[float]]:
    """PostgREST serializza vector come stringa '[0.1,0.2,...]'."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else None
        except json.JSONDecodeError:
            return None
    return None


async def lookup_cached_document(content_hash: str) -> Optional[Dict]:
    """
    Cerca un documento con lo stesso content_hash e ne rilegge i chunk.
    Ritorna None su miss o su qualunque errore (mai eccezioni al chiamante).
    """
    if not _search.SUPABASE_URL or not _search.SUPABASE_SERVICE_KEY:
        return None

    headers = {
        "apikey": _search.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {_search.SUPABASE_SERVICE_KEY}",
    }
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(
                f"{_search.SUPABASE_URL}/rest/v1/documenti",
                params={
                    "content_hash": f"eq.{content_hash}",
                    "select": "id,contenuto_testo,riassunto,n_chunks",
                    "limit": "1",
                },
                headers=headers,
                timeout=_search.SEARCH_HTTP_TIMEOUT,
            )
            if r.status_code >= 400:
                return None
            rows = r.json()
            if not rows:
                return None
            doc = rows[0]

            r2 = await http.get(
                f"{_search.SUPABASE_URL}/rest/v1/document_chunks",
                params={
                    "documento_id": f"eq.{doc['id']}",
                    "select": "chunk_index,contenuto,heading,embedding,discipline,tags,entities",
                    "order": "chunk_index.asc",
                },
                headers=headers,
                timeout=_search.SEARCH_HTTP_TIMEOUT,
            )
            if r2.status_code >= 400:
                return None
            raw_chunks = r2.json()
    except Exception as e:
        log.warning("cache_lookup_failed", extra={"err": str(e)})
        return None

    if not isinstance(raw_chunks, list) or not raw_chunks:
        return None

    chunks: List[Dict] = []
    for ch in raw_chunks:
        emb = _normalize_embedding(ch.get("embedding"))
        if emb is None:
            # Un chunk senza embedding valido invalida la cache: meglio
            # rigenerare tutto che restituire un documento parziale.
            return None
        chunks.append({
            "chunk_index": ch.get("chunk_index"),
            "contenuto": ch.get("contenuto"),
            "heading": ch.get("heading"),
            "embedding": emb,
            "discipline": ch.get("discipline") or [],
            "tags": ch.get("tags") or [],
            "entities": ch.get("entities"),
        })

    return {
        "documento_id": doc["id"],
        "markdown": doc.get("contenuto_testo") or "",
        "riassunto": doc.get("riassunto"),
        "chunks": chunks,
    }
