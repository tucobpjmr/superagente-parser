"""
Fase 2 — Retrieval multidisciplinare (POST /search).

Pipeline:
  1. Decomposizione LLM della domanda in sotto-domande con discipline pertinenti
     (tassonomia chiusa condivisa con enrichment.py)
  2. Query expansion leggera IT/EN nell'input dell'embedding (i documenti
     normativi mescolano le due lingue)
  3. Fan-out parallelo su Supabase RPC match_chunks (dense + FTS + RRF interno,
     boost morbido x1.5 per disciplina — mai filtro rigido)
  4. Fusione RRF + deduplica UUID tra sotto-domande
  5. Re-ranking LLM sulla domanda originale completa
  6. Arricchimento risultati con nome_file (lookup documenti)

Richiede: OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

import httpx

from embeddings import generate_embeddings_batch, get_client as get_openai_client
from enrichment import get_taxonomy


log = logging.getLogger("parser.search")


SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

SEARCH_TOP_K = int(os.getenv("SEARCH_TOP_K", "8"))
SEARCH_DECOMPOSE = os.getenv("SEARCH_DECOMPOSE", "true").lower() in ("1", "true", "yes", "on")
SEARCH_RERANK = os.getenv("SEARCH_RERANK", "llm")  # "llm" | "none"
DECOMPOSE_MODEL = os.getenv("DECOMPOSE_MODEL", "gpt-4o-mini")
RERANK_MODEL = os.getenv("RERANK_MODEL", "gpt-4o-mini")
SEARCH_HTTP_TIMEOUT = float(os.getenv("SEARCH_HTTP_TIMEOUT", "15"))
SEARCH_LLM_TIMEOUT = float(os.getenv("SEARCH_LLM_TIMEOUT", "20"))

# Limiti hard per evitare abuse
MAX_QUERY_CHARS = 2000
MAX_SUBQUERIES = 6
MAX_TOP_K = 30


DECOMPOSE_SYSTEM = """Scomponi domande complesse di un'agenzia viaggi in sotto-domande indipendenti per recupero RAG multidisciplinare.

Discipline disponibili (usa SOLO queste): {taxonomy}

Restituisci da 1 a {max_n} sotto-domande, ciascuna focalizzata su un singolo aspetto (es. profilo contrattuale, copertura assicurativa, trattamento fiscale). Se la domanda è già atomica, restituisci un solo elemento con il testo originale.

Per ogni sotto-domanda fornisci anche una riformulazione inglese sintetica ("testo_en"): i documenti normativi mescolano italiano e inglese e la variante EN migliora il retrieval semantico.

Rispondi SOLO con JSON valido:
{{"sotto_domande": [{{"testo": "<sotto-domanda in italiano>", "testo_en": "<english reformulation>", "discipline": ["<disciplina>", ...]}}, ...]}}

"discipline" può essere vuota se nessuna disciplina è chiaramente attribuibile."""


async def decompose_query(domanda: str, max_n: int = 4) -> List[Dict]:
    """
    Scompone la domanda in sotto-domande con discipline target (dalla
    tassonomia chiusa) e riformulazione EN per l'embedding.
    Fallback: la domanda originale come unica sotto-domanda.
    """
    fallback = [{"testo": domanda, "testo_en": "", "discipline": []}]
    if not SEARCH_DECOMPOSE or not os.getenv("OPENAI_API_KEY"):
        return fallback

    taxonomy = get_taxonomy()
    try:
        client = get_openai_client()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=DECOMPOSE_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": DECOMPOSE_SYSTEM.format(
                            taxonomy=", ".join(taxonomy), max_n=max_n
                        ),
                    },
                    {"role": "user", "content": f"Domanda: {domanda}"},
                ],
            ),
            timeout=SEARCH_LLM_TIMEOUT,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
    except Exception as e:
        log.warning("decompose_failed", extra={"err": str(e)})
        return fallback

    items = data.get("sotto_domande")
    if not isinstance(items, list) or not items:
        return fallback

    valid = set(taxonomy)
    out: List[Dict] = []
    for it in items[:max_n]:
        if not isinstance(it, dict):
            continue
        testo = (it.get("testo") or "").strip()
        if not testo:
            continue
        disc_raw = it.get("discipline") or []
        if not isinstance(disc_raw, list):
            disc_raw = []
        disc = []
        for d in disc_raw:
            d = str(d).strip().lower()
            if d in valid and d not in disc:
                disc.append(d)
        out.append({
            "testo": testo[:MAX_QUERY_CHARS],
            "testo_en": str(it.get("testo_en") or "").strip()[:MAX_QUERY_CHARS],
            "discipline": disc,
        })

    return out or fallback


def _supabase_headers() -> Dict[str, str]:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


async def call_match_chunks(
    http: httpx.AsyncClient,
    embedding: List[float],
    query_text: str,
    discipline: Optional[List[str]],
    match_count: int,
    rrf_k: int = 50,
) -> List[Dict]:
    """Chiama la RPC Supabase match_chunks (ibrida dense+FTS+RRF, boost disciplina)."""
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
        headers=_supabase_headers(),
        timeout=SEARCH_HTTP_TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase RPC {r.status_code}: {r.text[:300]}")
    data = r.json()
    return data if isinstance(data, list) else []


async def fetch_document_names(http: httpx.AsyncClient, documento_ids: List[str]) -> Dict[str, str]:
    """Lookup nome_file dei documenti coinvolti. Best-effort: errori → mappa vuota."""
    ids = sorted({d for d in documento_ids if d})
    if not ids:
        return {}
    try:
        r = await http.get(
            f"{SUPABASE_URL}/rest/v1/documenti",
            params={"id": f"in.({','.join(ids)})", "select": "id,nome_file"},
            headers=_supabase_headers(),
            timeout=SEARCH_HTTP_TIMEOUT,
        )
        if r.status_code >= 400:
            return {}
        return {row["id"]: row.get("nome_file") for row in r.json() if row.get("id")}
    except Exception as e:
        log.warning("fetch_document_names_failed", extra={"err": str(e)})
        return {}


def rrf_fuse(result_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """Reciprocal Rank Fusion + deduplica UUID tra N liste ordinate."""
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
    "Ordina gli indici dal più al meno rilevante rispetto alla domanda e ometti "
    "quelli irrilevanti.\n"
    'Rispondi SOLO con JSON valido: {"ranking": [<indice_int>, ...]}.'
)


async def rerank_llm(domanda: str, candidates: List[Dict], top_k: int) -> List[Dict]:
    """Re-ranking LLM sulla domanda originale. In caso di errore: top_k per RRF."""
    if not candidates:
        return []
    if not os.getenv("OPENAI_API_KEY"):
        return candidates[:top_k]

    cand = candidates[: min(len(candidates), 20)]
    blob = "\n\n".join(
        f"[{i}] sezione: {c.get('heading') or '-'}\n{(c.get('contenuto') or '')[:500]}"
        for i, c in enumerate(cand)
    )

    try:
        client = get_openai_client()
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=RERANK_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": RERANK_SYSTEM},
                    {"role": "user", "content": f"Domanda: {domanda}\n\nChunk:\n{blob}"},
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
    domanda: str,
    top_k: Optional[int] = None,
    discipline: Optional[List[str]] = None,
    rerank: Optional[bool] = None,
    rrf_k: int = 60,
) -> Dict:
    """
    Pipeline completa: decompose → embed (IT+EN) → fan-out hybrid → RRF → rerank.

    `discipline` (opzionale, dal chiamante) si somma a quelle individuate dalla
    decomposizione: boost morbido via match_chunks, mai filtro rigido.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non impostate")

    top_k = max(1, min(top_k if top_k is not None else SEARCH_TOP_K, MAX_TOP_K))
    do_rerank = rerank if rerank is not None else (SEARCH_RERANK == "llm")
    caller_disc = [d.strip().lower() for d in (discipline or []) if d and d.strip()]
    per_sub_k = top_k * 2

    # 1. Decomposizione
    subqs = await decompose_query(domanda, max_n=MAX_SUBQUERIES)

    # 2. Embedding (batch unico): input arricchito con la riformulazione EN
    embed_inputs = [
        f"{sq['testo']}\n{sq['testo_en']}" if sq.get("testo_en") else sq["testo"]
        for sq in subqs
    ]
    embeddings = await generate_embeddings_batch(embed_inputs)

    # 3. Fan-out parallelo su match_chunks + 4. fusione RRF
    async with httpx.AsyncClient() as http:
        tasks = []
        for sq, emb in zip(subqs, embeddings):
            boost = list(dict.fromkeys(sq["discipline"] + caller_disc))
            tasks.append(
                call_match_chunks(
                    http, emb, sq["testo"], boost or None,
                    match_count=per_sub_k, rrf_k=50,
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

        clean_results: List[List[Dict]] = []
        errors: List[Dict] = []
        for sq, r in zip(subqs, results):
            if isinstance(r, Exception):
                log.warning("subquery_failed", extra={"subq": sq["testo"], "err": str(r)})
                errors.append({"sotto_domanda": sq["testo"], "error": str(r)})
                continue
            # Traccia quale sotto-domanda ha recuperato ciascun chunk
            for item in r:
                item.setdefault("sotto_domanda", sq["testo"])
            clean_results.append(r)

        if not clean_results:
            raise RuntimeError(f"Tutte le sotto-domande sono fallite: {errors}")

        fused = rrf_fuse(clean_results, k=rrf_k)

        # 5. Re-ranking sulla domanda originale
        if do_rerank and len(fused) > 1:
            final = await rerank_llm(domanda, fused, top_k=top_k)
        else:
            final = fused[:top_k]

        # 6. nome_file dei documenti coinvolti (best-effort)
        doc_names = await fetch_document_names(
            http, [c.get("documento_id") for c in final]
        )

    for c in final:
        c["nome_file"] = doc_names.get(c.get("documento_id"))

    return {
        "domanda": domanda,
        "sotto_domande": subqs,
        "n_candidati": len(fused),
        "risultati": final,
        "errors": errors or None,
    }
