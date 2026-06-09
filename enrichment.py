"""
Arricchimento LLM dei documenti per il RAG multidisciplinare.

Fase 1.3 — Contextual retrieval:
  generate_document_summary() produce un riassunto di UNA frase del documento,
  usato come contesto nell'input degli embedding. Una sola chiamata LLM per
  documento, su un modello economico (gpt-4o-mini di default).

Le fasi successive (1.1 classificazione multi-disciplina, 1.2 entità) si
aggiungeranno qui riusando lo stesso client e lo stesso flag ENRICHMENT_ENABLED.
"""

import logging
import os
from typing import Optional

from embeddings import get_client


log = logging.getLogger("parser")

# Flag globale per disattivare ogni chiamata LLM di arricchimento (fallback:
# comportamento pre-Fase-1). Accetta 1/true/yes/on (case-insensitive).
ENRICHMENT_ENABLED = os.getenv("ENRICHMENT_ENABLED", "true").lower() in ("1", "true", "yes", "on")
ENRICHMENT_MODEL = os.getenv("ENRICHMENT_MODEL", "gpt-4o-mini")

# Tronca l'input del riassunto: l'inizio di un documento è di norma sufficiente
# a coglierne il tema, e limita costo/latenza su documenti molto grandi.
SUMMARY_MAX_INPUT_CHARS = int(os.getenv("SUMMARY_MAX_INPUT_CHARS", "12000"))

_SUMMARY_SYSTEM = (
    "Sei un assistente che riassume documenti tecnici, normativi e commerciali. "
    "Riassumi il documento in UNA sola frase concisa in italiano (max 25 parole), "
    "indicando l'argomento principale. Rispondi solo con la frase, senza preamboli "
    "né virgolette."
)


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
