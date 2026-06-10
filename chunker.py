"""
Chunking del Markdown.
Strategia:
  1. Split per heading Markdown (#, ##, ###), tracciando la gerarchia completa
     (H1 > H2 > H3) come breadcrumb per ogni sezione
  2. Se un blocco supera chunk_size parole → fallback word-count con overlap
  3. Se un blocco è molto piccolo → merge con il successivo

Contextual retrieval (Fase 1.3):
  build_contextual_text() antepone al testo del chunk una riga di contesto
  (titolo documento + riassunto + breadcrump di sezione) usata SOLO come input
  dell'embedding. Il testo salvato in `contenuto` resta pulito.
"""

import re
from typing import List, Dict, Optional, Tuple


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _word_count(text: str) -> int:
    return len(text.split())


def _split_by_headings(markdown: str) -> List[Dict]:
    """
    Divide il markdown in sezioni per heading. Ogni sezione include il proprio
    heading e il percorso gerarchico completo (`heading_path`, es. "H1 > H2").
    """
    matches = list(HEADING_RE.finditer(markdown))

    if not matches:
        return [{"heading": None, "heading_path": None, "level": 0, "testo": markdown.strip()}]

    sections = []

    # Preambolo prima del primo heading
    if matches[0].start() > 0:
        preamble = markdown[: matches[0].start()].strip()
        if preamble:
            sections.append({"heading": None, "heading_path": None, "level": 0, "testo": preamble})

    # Stack degli antenati (level, heading) per costruire il breadcrumb
    stack: List[Tuple[int, str]] = []

    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2).strip()

        # Rimuovi dallo stack gli heading di pari o maggior livello: non sono
        # antenati di questa sezione.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        heading_path = " > ".join(h for _, h in stack)

        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections.append(
            {
                "heading": heading,
                "heading_path": heading_path,
                "level": level,
                "testo": markdown[start:end].strip(),
            }
        )

    return sections


def _split_by_words(text: str, chunk_size: int, overlap: int) -> List[str]:
    """Fallback: split per word-count con overlap."""
    words = text.split()
    if len(words) <= chunk_size:
        return [text]

    chunks = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    return chunks


def chunk_markdown(
    markdown: str,
    chunk_size: int = 500,
    overlap: int = 50,
    min_chunk_words: int = 50,
) -> List[Dict]:
    """
    Args:
        markdown: testo markdown da Markitdown
        chunk_size: parole target per chunk
        overlap: parole di overlap (solo in fallback word-count)
        min_chunk_words: chunk sotto questa soglia vengono fusi col successivo

    Returns:
        Lista di dict: { chunk_index, contenuto, heading, heading_path }
    """
    sections = _split_by_headings(markdown)
    raw_chunks = []

    for sec in sections:
        common = {"heading": sec["heading"], "heading_path": sec["heading_path"]}
        if _word_count(sec["testo"]) <= chunk_size:
            raw_chunks.append({"contenuto": sec["testo"], **common})
        else:
            # Sezione troppo grande → spezza per parole, mantieni heading come prefisso
            sub_texts = _split_by_words(sec["testo"], chunk_size, overlap)
            for sub in sub_texts:
                raw_chunks.append({"contenuto": sub, **common})

    # Merge chunk troppo piccoli col successivo.
    # Usa list+join invece di concatenazioni O(n²) e riassorbi l'ultima
    # coda piccola nel chunk precedente invece di lasciarla orfana.
    merged: List[Dict] = []
    buf_parts: List[str] = []
    buf_heading: Optional[str] = None
    buf_heading_path: Optional[str] = None
    buf_words: int = 0

    def _flush() -> Dict:
        return {
            "contenuto": "\n\n".join(buf_parts),
            "heading": buf_heading,
            "heading_path": buf_heading_path,
        }

    for ch in raw_chunks:
        wc = _word_count(ch["contenuto"])
        if not buf_parts:
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_heading_path = ch["heading_path"]
            buf_words = wc
            continue
        if buf_words < min_chunk_words:
            buf_parts.append(ch["contenuto"])
            buf_words += wc
        else:
            merged.append(_flush())
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_heading_path = ch["heading_path"]
            buf_words = wc

    if buf_parts:
        tail = _flush()
        if merged and buf_words < min_chunk_words:
            # Riassorbi coda orfana nel chunk precedente
            merged[-1]["contenuto"] = merged[-1]["contenuto"] + "\n\n" + tail["contenuto"]
        else:
            merged.append(tail)

    # Aggiungi chunk_index
    return [
        {
            "chunk_index": i,
            "contenuto": c["contenuto"],
            "heading": c["heading"],
            "heading_path": c["heading_path"],
        }
        for i, c in enumerate(merged)
    ]


def build_contextual_text(
    contenuto: str,
    heading_path: Optional[str],
    titolo: Optional[str] = None,
    riassunto: Optional[str] = None,
) -> str:
    """
    Costruisce l'input dell'embedding arricchito col contesto documentale.
    Il risultato NON viene salvato: è solo ciò che l'embedder "vede", così un
    chunk come "Art. 5 — rimborso entro 14 giorni" porta con sé il tema del
    documento e la sua collocazione gerarchica (+precisione cross-dominio).

    Formato: "[Doc: <titolo> — <riassunto> — Sezione: <H1 > H2 > H3>]\\n\\n<testo>"
    """
    segments: List[str] = []
    if titolo:
        segments.append(f"Doc: {titolo}")
    if riassunto:
        segments.append(riassunto)
    if heading_path:
        segments.append(f"Sezione: {heading_path}")

    if not segments:
        return contenuto
    return f"[{' — '.join(segments)}]\n\n{contenuto}"
