"""
Chunking del Markdown.
Strategia:
  1. Split per heading Markdown (#, ##, ###)
  2. Se un blocco supera chunk_size parole → fallback word-count con overlap
  3. Se un blocco è molto piccolo → merge con il successivo
"""

import re
from typing import List, Dict


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _word_count(text: str) -> int:
    return len(text.split())


def _split_by_headings(markdown: str) -> List[Dict]:
    """Divide il markdown in sezioni per heading. Ogni sezione include il proprio heading."""
    matches = list(HEADING_RE.finditer(markdown))

    if not matches:
        return [{"heading": None, "level": 0, "testo": markdown.strip()}]

    sections = []

    # Preambolo prima del primo heading
    if matches[0].start() > 0:
        preamble = markdown[: matches[0].start()].strip()
        if preamble:
            sections.append({"heading": None, "level": 0, "testo": preamble})

    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        sections.append(
            {
                "heading": m.group(2).strip(),
                "level": len(m.group(1)),
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
        Lista di dict: { chunk_index, contenuto, heading }
    """
    sections = _split_by_headings(markdown)
    raw_chunks = []

    for sec in sections:
        if _word_count(sec["testo"]) <= chunk_size:
            raw_chunks.append({"contenuto": sec["testo"], "heading": sec["heading"]})
        else:
            # Sezione troppo grande → spezza per parole, mantieni heading come prefisso
            sub_texts = _split_by_words(sec["testo"], chunk_size, overlap)
            for sub in sub_texts:
                raw_chunks.append({"contenuto": sub, "heading": sec["heading"]})

    # Merge chunk troppo piccoli col successivo.
    # Fix: usa list+join invece di concatenazioni O(n²) e riassorbi l'ultima
    # coda piccola nel chunk precedente invece di lasciarla orfana.
    merged: List[Dict] = []
    buf_parts: List[str] = []
    buf_heading = None
    buf_words: int = 0

    for ch in raw_chunks:
        wc = _word_count(ch["contenuto"])
        if not buf_parts:
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_words = wc
            continue
        if buf_words < min_chunk_words:
            buf_parts.append(ch["contenuto"])
            buf_words += wc
        else:
            merged.append({"contenuto": "\n\n".join(buf_parts), "heading": buf_heading})
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_words = wc

    if buf_parts:
        tail = "\n\n".join(buf_parts)
        if merged and buf_words < min_chunk_words:
            # Riassorbi coda orfana nel chunk precedente
            merged[-1]["contenuto"] = merged[-1]["contenuto"] + "\n\n" + tail
        else:
            merged.append({"contenuto": tail, "heading": buf_heading})

    # Aggiungi chunk_index
    return [
        {"chunk_index": i, "contenuto": c["contenuto"], "heading": c["heading"]}
        for i, c in enumerate(merged)
    ]
