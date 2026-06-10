"""
Pipeline di ricerca ibrida RAG:
  1. Decomposizione LLM della domanda in sotto-domande per disciplina
  2. Fan-out parallelo su Supabase RPC match_chunks (vettoriale + FTS + RRF interno)
  3. Fusione RRF dei risultati tra sotto-domande
  4. Re-ranking LLM opzionale dei candidati

Richiede: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

import httpx

from embeddings import generate_embeddings_batch, get_client as get_openai_client


log = logging.getLogger("parser.search")


SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

SEARCH_LLM_MODEL = os.getenv("SEARCH_LLM_MODEL", "gpt-4o-mini")
SEARCH_HTTP_TIMEOUT = float(os.getenv("SEARCH_HTTP_TIMEOUT", "15"))
SEARCH_LLM_TIMEOUT = float(os.getenv("SEARCH_LLM_TIMEOUT", "20"))

# Discipline note nel dominio (modulo: generale/controversie/meteo/visti).
# L'LLM può comunque usarne altre se la domanda lo richiede.
KNOWN_DISCIPLINES = ["generale", "controversie", "meteo", "visti"]

# Limiti hard per evitare abuse
MAX_QUERY_CHARS = 2000
MAX_SUBQUERIES = 6
MAX_TOP_K = 30


DECOMPOSE_SYSTEM = (
    "Sei un assistente che scompone domande complesse in sotto-domande indipendenti "
    "per recupero RAG su una knowledge base di consulenza viaggi.\n"
    "Discipline note: {disciplines}.\n"
    "Restituisci da 1 a {max_n} sotto-domande, ciascuna focalizzata su un singolo aspetto/disciplina.\n"
    "Se la domanda è già atomica, restituisci un solo elemento con il testo originale.\n\n"
    "Formato di risposta JSON: "
    '{{"subqueries": [{{"text": "<sotto-domanda>", "discipline": ["<disc1>", "..."]}}, ...]}}\n'
    "Il campo 'discipline' è una lista (può essere vuota se non chiaramente attribuibile)."
)


async def decompose_query(query: str, max_n: int = 3) -> List[Dict]:
    """
    Scompone una domanda in sotto-domande con disciplina target.
    Fallback: se l'LLM fallisce, ritorna la query originale come unica sotto-domanda.
    """
    fallback = [{"text": query, "discipline": []}]
    if not os.getenv("OPENAI_API_KEY"):
        return fallback

    try:
        client = get_openai_client()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=SEARCH_LLM_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": DECOMPOSE_SYSTEM.format(
                            disciplines=", ".join(KNOWN_DISCIPLINES),
                            max_n=max_n,
                        ),
                    },
                    {"role": "user", "content": f"Domanda: {query}"},
                ],
            ),
            timeout=SEARCH_LLM_TIMEOUT,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("decompose_failed", extra={"err": str(e)})
        return fallback

    items = data.get("subqueries")
    if not isinstance(items, list) or not items:
        return fallback

    out: List[Dict] = []
    for it in items[:max_n]:
        if not isinstance(it, dict):
            continue
        text = (it.get("text") or "").strip()
        if not text:
            continue
        disc = it.get("discipline") or []
        if not isinstance(disc, list):
            disc = []
        disc = [str(d).strip().lower() for d in disc if isinstance(d, (str, int))]
        out.append({"text": text[:MAX_QUERY_CHARS], "discipline": disc})

    return out or fallback


async def call_match_chunks(
    http: httpx.AsyncClient,
    embedding: List[float],
    query_text: str,
    discipline: Optional[List[str]],
    match_count: int,
    rrf_k: int = 50,
) -> List[Dict]:
    """Chiama la RPC Supabase match_chunks (ibrida vettore+FTS+RRF)."""
    payload = {
        "query_embedding": embedding,
        "query_text": query_text,
        "filtro_discipline": discipline if discipline else None,
        "match_count": match_count,
        "rrf_k": rrf_k,
    }
    r = await http.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_chunks",
        json=payload,
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        timeout=SEARCH_HTTP_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase RPC {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data if isinstance(data, list) else []


def rrf_fuse(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """Reciprocal Rank Fusion tra N liste ordinate. Chiave: campo 'id'."""
    scores: Dict[str, float] = {}
    by_id: Dict[str, Dict] = {}
    for results in result_lists:
        for rank, item in enumerate(results, start=1):
            cid = item.get("id")
            if not cid:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            if cid not in by_id:
                by_id[cid] = item
    fused: List[Dict] = []
    for cid, sc in sorted(scores.items(), key=lambda x: -x[1]):
        item = dict(by_id[cid])
        item["rrf_score"] = sc
        fused.append(item)
    return fused


RERANK_SYSTEM = (
    "Sei un re-ranker per RAG. Ricevi una domanda e una lista di chunk numerati. "
    "Ordina gli indici dal più al meno rilevante e ometti quelli irrilevanti.\n"
    'Formato risposta JSON: {"ranking": [<indice_int>, ...]}.'
)


async def rerank_llm(query: str, candidates: List[Dict], top_k: int) -> List[Dict]:
    """Re-ranking LLM dei candidati. In caso di errore ritorna i primi top_k per RRF."""
    if not candidates:
        return []
    if not os.getenv("OPENAI_API_KEY"):
        return candidates[:top_k]

    cand = candidates[: min(len(candidates), 20)]
    blob = "\n\n".join(
        f"[{i}] heading: {c.get('heading') or '-'}\n{(c.get('contenuto') or '')[:500]}"
        for i, c in enumerate(cand)
    )

    try:
        client = get_openai_client()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=SEARCH_LLM_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": RERANK_SYSTEM},
                    {"role": "user", "content": f"Domanda: {query}\n\nChunk:\n{blob}"},
                ],
            ),
            timeout=SEARCH_LLM_TIMEOUT,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("rerank_failed", extra={"err": str(e)})
        return candidates[:top_k]

    ranking = data.get("ranking")
    if not isinstance(ranking, list):
        return candidates[:top_k]

    out: List[Dict] = []
    seen = set()
    for idx in ranking:
        if isinstance(idx, int) and 0 <= idx < len(cand) and idx not in seen:
            out.append(cand[idx])
            seen.add(idx)
            if len(out) >= top_k:
                break
    # Completa con i restanti per RRF se l'LLM ne ha esclusi troppi
    if len(out) < top_k:
        for i, c in enumerate(cand):
            if i not in seen:
                out.append(c)
                if len(out) >= top_k:
                    break
    return out


async def search_pipeline(
    query: str,
    top_k: int = 8,
    n_subqueries: int = 3,
    per_sub_k: int = 12,
    rerank: bool = True,
    rrf_k: int = 60,
) -> Dict:
    """Pipeline completa: decompose → fan-out hybrid → RRF fusion → rerank."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non impostate")

    top_k = max(1, min(top_k, MAX_TOP_K))
    n_subqueries = max(1, min(n_subqueries, MAX_SUBQUERIES))
    per_sub_k = max(top_k, min(per_sub_k, 40))

    # 1. Decomposizione
    subqs = await decompose_query(query, max_n=n_subqueries)

    # 2. Embedding di tutte le sotto-domande (batch unico)
    texts = [sq["text"] for sq in subqs]
    embeddings = await generate_embeddings_batch(texts)

    # 3. Fan-out parallelo su match_chunks
    async with httpx.AsyncClient() as http:
        tasks = [
            call_match_chunks(
                http,
                emb,
                sq["text"],
                sq["discipline"] or None,
                match_count=per_sub_k,
                rrf_k=50,
            )
            for sq, emb in zip(subqs, embeddings)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    clean_results: List[List[Dict]] = []
    errors: List[Dict] = []
    for sq, r in zip(subqs, results):
        if isinstance(r, Exception):
            log.warning("subquery_failed", extra={"subq": sq["text"], "err": str(r)})
            errors.append({"subquery": sq["text"], "error": str(r)})
            continue
        clean_results.append(r)

    if not clean_results:
        raise RuntimeError(f"Tutte le sotto-query sono fallite: {errors}")

    # 4. Fusione RRF
    fused = rrf_fuse(clean_results, k=rrf_k)

    # 5. Re-ranking opzionale
    if rerank and len(fused) > 1:
        final = await rerank_llm(query, fused, top_k=top_k)
    else:
        final = fused[:top_k]

    return {
        "query": query,
        "subqueries": subqs,
        "n_candidates": len(fused),
        "results": final,
        "errors": errors or None,
    }
