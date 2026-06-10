"""
Fase 3.1 — Sintesi con citazioni (POST /answer).

Pipeline:
  1. Recupera top-k chunk con search.search_pipeline (decompose + RRF + rerank)
  2. Raggruppa i chunk per disciplina
  3. Sintetizza con LLM con istruzione di:
     - integrare informazioni di discipline diverse
     - segnalare interazioni/conflitti tra discipline
     - citare con [n] dove n è l'indice del chunk
     - dichiarare l'insufficienza delle fonti invece di allucinare
  4. Ritorna risposta + citazioni strutturate (chunk_id, nome_file, sezione,
     disciplina)

Le citazioni in risposta usano la notazione [1], [2], … allineata all'array
`citazioni` (1-based).
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional

from embeddings import get_client as get_openai_client
from search import search_pipeline


log = logging.getLogger("parser.answer")


ANSWER_MODEL = os.getenv("ANSWER_MODEL", "gpt-4o")
ANSWER_LLM_TIMEOUT = float(os.getenv("ANSWER_LLM_TIMEOUT", "45"))
ANSWER_CHUNK_CHARS = int(os.getenv("ANSWER_CHUNK_CHARS", "1500"))
ANSWER_MAX_CITATIONS = int(os.getenv("ANSWER_MAX_CITATIONS", "12"))


ANSWER_SYSTEM = """Sei un consulente esperto multidisciplinare per un'agenzia viaggi. Rispondi a domande di consulenza usando ESCLUSIVAMENTE le fonti fornite, mai conoscenza esterna.

Le fonti sono raggruppate per disciplina (contrattualistica, fiscalita, assicurazioni, ecc.) e numerate [1], [2], ….

Regole inderogabili:
1. Integra le informazioni provenienti da discipline diverse in una risposta coerente.
2. Quando due discipline interagiscono o danno indicazioni in tensione tra loro, segnalalo esplicitamente (es. "sul piano contrattuale ... ma la disciplina fiscale impone ...").
3. Cita la fonte per ogni affermazione non ovvia usando la notazione [n] inline (es. "Il rimborso è dovuto entro 14 giorni [3].").
4. Se le fonti sono insufficienti, parziali o non coprono un aspetto della domanda, DICHIARALO esplicitamente invece di inventare. Esempi: "Le fonti disponibili non chiariscono il regime IVA applicabile a questo caso specifico."
5. Risposta in italiano, tono professionale, struttura per punti quando aiuta la chiarezza."""


def _build_context(chunks: List[Dict]) -> str:
    """
    Raggruppa i chunk per disciplina principale e li serializza con indice [n].
    L'indice è 1-based e allineato all'ordine dei chunk in input (così
    l'array `citazioni` in risposta usa gli stessi numeri).
    """
    groups: Dict[str, List[tuple]] = {}
    for i, c in enumerate(chunks, start=1):
        disc_list = c.get("discipline") or []
        disc = disc_list[0] if disc_list else "altro"
        groups.setdefault(disc, []).append((i, c))

    parts: List[str] = []
    for disc, items in groups.items():
        parts.append(f"\n### Disciplina: {disc}\n")
        for i, c in items:
            heading = c.get("heading") or "—"
            nome_file = c.get("nome_file") or "documento"
            testo = (c.get("contenuto") or "")[:ANSWER_CHUNK_CHARS]
            parts.append(
                f"[{i}] (fonte: {nome_file} — {heading})\n{testo}\n"
            )
    return "\n".join(parts)


def _build_citations(chunks: List[Dict]) -> List[Dict]:
    out = []
    for i, c in enumerate(chunks, start=1):
        disc_list = c.get("discipline") or []
        out.append({
            "n": i,
            "chunk_id": c.get("id"),
            "documento_id": c.get("documento_id"),
            "nome_file": c.get("nome_file"),
            "sezione": c.get("heading"),
            "discipline": disc_list,
            "disciplina": disc_list[0] if disc_list else None,
        })
    return out


async def synthesize_answer(domanda: str, chunks: List[Dict]) -> str:
    """Chiama il modello di sintesi. Fallback: messaggio esplicito su errore."""
    if not chunks:
        return ("Non ho trovato fonti pertinenti per rispondere a questa domanda. "
                "Verifica che la knowledge base contenga documenti sull'argomento.")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY non impostata")

    context = _build_context(chunks)
    user_msg = f"Domanda: {domanda}\n\nFonti disponibili:{context}"

    client = get_openai_client()
    resp = await asyncio.wait_for(
        client.chat.completions.create(
            model=ANSWER_MODEL,
            temperature=0.1,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        ),
        timeout=ANSWER_LLM_TIMEOUT,
    )
    return (resp.choices[0].message.content or "").strip()


async def answer_pipeline(
    domanda: str,
    top_k: Optional[int] = None,
    discipline: Optional[List[str]] = None,
) -> Dict:
    """Search + synthesize. Ritorna {risposta, citazioni, retrieval}."""
    search_result = await search_pipeline(
        domanda=domanda,
        top_k=top_k,
        discipline=discipline,
    )
    chunks = (search_result.get("risultati") or [])[:ANSWER_MAX_CITATIONS]
    risposta = await synthesize_answer(domanda, chunks)
    return {
        "domanda": domanda,
        "risposta": risposta,
        "citazioni": _build_citations(chunks),
        "retrieval": {
            "sotto_domande": search_result.get("sotto_domande"),
            "n_candidati": search_result.get("n_candidati"),
            "errors": search_result.get("errors"),
        },
    }
