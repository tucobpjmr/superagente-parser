"""
Arricchimento LLM dei documenti per il RAG multidisciplinare.

Fase 1.3 — Contextual retrieval:
  generate_document_summary() produce un riassunto di UNA frase del documento,
  usato come contesto nell'input degli embedding. Una sola chiamata LLM per
  documento, su un modello economico (gpt-4o-mini di default).

Fase 1.1/1.2 — Classificazione multi-disciplina + entità:
  classify_chunks() assegna a ogni chunk N discipline da una tassonomia
  chiusa, tag liberi e le entità "ponte" tra discipline (riferimenti
  normativi, importi, date, organismi). Una chiamata LLM per batch di chunk,
  batch eseguiti in parallelo.

Tutte le chiamate sono gated da ENRICHMENT_ENABLED; il chiamante (main.py)
le avvolge in wrapper "safe" perché l'arricchimento non deve mai bloccare
l'ingestion.
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

import openai
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

from embeddings import get_client


log = logging.getLogger("parser")

# Flag globale per disattivare ogni chiamata LLM di arricchimento (fallback:
# comportamento pre-Fase-1). Accetta 1/true/yes/on (case-insensitive).
ENRICHMENT_ENABLED = os.getenv("ENRICHMENT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
ENRICHMENT_MODEL = os.getenv("ENRICHMENT_MODEL", "gpt-4o-mini")

# Tronca l'input del riassunto: l'inizio di un documento è di norma sufficiente
# a coglierne il tema, e limita costo/latenza su documenti molto grandi.
SUMMARY_MAX_INPUT_CHARS = int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "12000"))

# Chunk per singola chiamata di classificazione e caratteri massimi di ogni
# chunk inviati al classificatore (la classificazione non richiede il testo
# integrale).
ENRICHMENT_BATCH_SIZE = int(os.getenv("ENRICHMENT_BATCH_SIZE", "16"))
ENRICHMENT_CHUNK_CHARS = int(os.getenv("ENRICHMENT_CHUNK_CHARS", "2000"))

# Tassonomia chiusa: l'LLM sceglie SOLO da questa lista (evita la
# proliferazione di etichette). Sovrascrivibile via env CSV.
_DEFAULT_TAXONOMY = [
    "fiscalita",
    "contrattualistica",
    "assicurazioni",
    "trasporti",
    "turismo",
    "normativa-ue",
    "privacy",
    "contabilita",
    "visti-documenti",
    "dogane",
    "salute-sicurezza",
    "controversie",
]


def get_taxonomy() -> List[str]:
    raw = os.getenv("DISCIPLINE_TAXONOMY", "")
    custom = [d.strip().lower() for d in raw.split(",") if d.strip()]
    return custom or list(_DEFAULT_TAXONOMY)


_SUMMARY_SYSTEM = (
    "Sei un assistente che riassume documenti tecnici, normativi e commerciali. "
    "Riassumi il documento in UNA sola frase concisa in italiano (max 25 parole), "
    "indicando l'argomento principale. Rispondi solo con la frase, senza preamboli "
    "né virgolette."
)

_CLASSIFY_SYSTEM = """Classifichi frammenti di documenti per un sistema di knowledge retrieval multidisciplinare di un'agenzia viaggi.

Per OGNI frammento numerato restituisci:
- "discipline": le discipline pertinenti scelte ESCLUSIVAMENTE da questa lista: {taxonomy}. Un frammento può (e spesso deve) appartenere a più discipline. Lista vuota se nessuna è pertinente.
- "tags": 0-5 parole chiave libere in italiano, minuscole (argomenti specifici non coperti dalle discipline).
- "entities": entità "ponte" citate nel testo, con queste chiavi (ometti le chiavi vuote):
  - "riferimenti_normativi": leggi, decreti, articoli, direttive, regolamenti (es. "Dlgs 62/2024", "art. 41 CdT", "Direttiva UE 2015/2302")
  - "importi": importi monetari con valuta
  - "date": date o scadenze rilevanti
  - "organismi": enti, autorità, organizzazioni citate

Rispondi SOLO con JSON valido nel formato:
{{"chunks": [{{"i": 0, "discipline": [...], "tags": [...], "entities": {{...}}}}, ...]}}
Includi un oggetto per ogni frammento, nello stesso ordine, con "i" uguale al numero del frammento."""


async def generate_document_summary(markdown: str) -> Optional[str]:
    """
    Genera un riassunto di una frase del documento. Ritorna None se
    l'arricchimento è disattivato o se il testo è vuoto.

    Solleva eccezioni in caso di errore LLM: il chiamante decide se degradare
    (vedi _safe_document_summary in main.py) — il contextual chunking non deve
    mai bloccare l'ingestion.
    """
    if not ENRICHMENT_ENABLED:
        return None

    text = (markdown or "").strip()
    if not text:
        return None

    excerpt = text[:SUMMARY_MAX_INPUT_CHARS]
    client = get_client()
    resp = await client.chat.completions.create(
        model=ENRICHMENT_MODEL,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": excerpt},
        ],
        temperature=0,
        max_tokens=80,
    )
    summary = (resp.choices[0].message.content or "").strip()
    return summary or None


# ---------------------------------------------------------------------------
# Classificazione multi-disciplina + entità (Fase 1.1/1.2)
# ---------------------------------------------------------------------------

def empty_enrichment() -> Dict:
    """Risultato neutro: il chiamante vi unirà il modulo legacy."""
    return {"discipline": [], "tags": [], "entities": None}


def _parse_classification(content: str, n_chunks: int, taxonomy: List[str]) -> List[Dict]:
    """
    Valida la risposta JSON dell'LLM e la normalizza in una lista allineata
    per posizione ai chunk di input. Qualunque elemento malformato degrada a
    empty_enrichment() senza sollevare.
    """
    results = [empty_enrichment() for _ in range(n_chunks)]
    try:
        payload = json.loads(content)
        items = payload.get("chunks", [])
    except (json.JSONDecodeError, AttributeError):
        log.warning("classify_parse_failed", extra={"content_head": content[:200]})
        return results

    taxonomy_set = set(taxonomy)
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("i", pos)
        if not isinstance(idx, int) or not (0 <= idx < n_chunks):
            idx = pos
        if idx >= n_chunks:
            continue

        raw_discipline = item.get("discipline") or []
        discipline: List[str] = []
        if isinstance(raw_discipline, list):
            seen = set()
            for d in raw_discipline:
                if not isinstance(d, str):
                    continue
                dl = d.strip().lower()
                if dl in taxonomy_set and dl not in seen:
                    seen.add(dl)
                    discipline.append(dl)

        tags = item.get("tags") or []
        if isinstance(tags, list):
            tags = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()][:8]
        else:
            tags = []

        entities = item.get("entities")
        if isinstance(entities, dict):
            # Tieni solo liste di stringhe non vuote; None se non resta nulla
            entities = {
                k: [str(v) for v in vals if str(v).strip()]
                for k, vals in entities.items()
                if isinstance(vals, list) and any(str(v).strip() for v in vals)
            } or None
        else:
            entities = None

        results[idx] = {"discipline": discipline, "tags": tags, "entities": entities}

    return results


@retry(
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type(
        (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError)
    ),
    reraise=True,
)
async def _classify_batch(texts: List[str], taxonomy: List[str], hint: Optional[str]) -> List[Dict]:
    client = get_client()
    numbered = "\n\n".join(
        f"--- Frammento {i} ---\n{t[:ENRICHMENT_CHUNK_CHARS]}" for i, t in enumerate(texts)
    )
    user_msg = numbered
    if hint:
        user_msg = f"(Hint del caller: il documento è stato caricato nel modulo '{hint}')\n\n{numbered}"

    resp = await client.chat.completions.create(
        model=ENRICHMENT_MODEL,
        messages=[
            {"role": "system", "content": _CLASSIFY_SYSTEM.format(taxonomy=", ".join(taxonomy))},
            {"role": "user", "content": user_msg},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content or ""
    return _parse_classification(content, len(texts), taxonomy)


async def classify_chunks(texts: List[str], hint: Optional[str] = None) -> List[Dict]:
    """
    Classifica i chunk in batch paralleli. Ritorna una lista allineata
    all'input di dict {discipline, tags, entities}.

    Se ENRICHMENT_ENABLED è false ritorna subito risultati neutri. Un batch
    che fallisce dopo i retry degrada ai risultati neutri per i SUOI chunk
    senza far fallire gli altri batch.
    """
    if not texts:
        return []
    if not ENRICHMENT_ENABLED:
        return [empty_enrichment() for _ in texts]

    taxonomy = get_taxonomy()
    batches = [texts[i : i + ENRICHMENT_BATCH_SIZE] for i in range(0, len(texts), ENRICHMENT_BATCH_SIZE)]
    outcomes = await asyncio.gather(
        *[_classify_batch(b, taxonomy, hint) for b in batches],
        return_exceptions=True,
    )

    results: List[Dict] = []
    for batch, outcome in zip(batches, outcomes):
        if isinstance(outcome, BaseException):
            log.warning("classify_batch_failed", extra={"error": str(outcome), "n_chunks": len(batch)})
            results.extend(empty_enrichment() for _ in batch)
        else:
            results.extend(outcome)
    return results
