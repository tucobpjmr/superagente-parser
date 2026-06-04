"""
Chunking del Markdown.
Strategia:
  1. Split per heading Markdown (#, ##, ###)
  2. Se un blocco supera chunk_size parole → fallback word-count con overlap
  3. Se un blocco è molto piccolo → merge con il successivo
"""

import re
from typing import List, Dict, Tuple


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


def _split_by_words(words: List[str], chunk_size: int, overlap: int) -> List[Tuple[str, int]]:
    """Fallback: split per word-count con overlap.

    Accetta lista parole già calcolata dal chiamante (evita un secondo split).
    Ritorna lista di (testo, n_parole).
    """
    if len(words) <= chunk_size:
        return [(" ".join(words), len(words))]

    result: List[Tuple[str, int]] = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        end = min(i + chunk_size, len(words))
        result.append((" ".join(words[i:end]), end - i))
        if end >= len(words):
            break
    return result


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
    raw_chunks: List[Dict] = []

    for sec in sections:
        words = sec["testo"].split()  # split una volta sola per sezione
        n_words = len(words)

        if n_words <= chunk_size:
            raw_chunks.append({"contenuto": sec["testo"], "heading": sec["heading"], "_wc": n_words})
        else:
            # Sezione troppo grande → spezza per parole, mantieni heading
            for sub_text, sub_wc in _split_by_words(words, chunk_size, overlap):
                raw_chunks.append({"contenuto": sub_text, "heading": sec["heading"], "_wc": sub_wc})

    # Merge chunk troppo piccoli col successivo.
    # Accumulo in lista per evitare concatenazioni di stringhe intermedie.
    merged: List[Dict] = []
    buf_parts: List[str] = []
    buf_heading = None
    buf_wc = 0

    for ch in raw_chunks:
        if not buf_parts:
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_wc = ch["_wc"]
            continue

        if buf_wc < min_chunk_words:
            buf_parts.append(ch["contenuto"])
            buf_wc += ch["_wc"]
        else:
            merged.append({"contenuto": "\n\n".join(buf_parts), "heading": buf_heading})
            buf_parts = [ch["contenuto"]]
            buf_heading = ch["heading"]
            buf_wc = ch["_wc"]

    if buf_parts:
        merged.append({"contenuto": "\n\n".join(buf_parts), "heading": buf_heading})

    # Aggiungi chunk_index
    return [
        {"chunk_index": i, "contenuto": c["contenuto"], "heading": c["heading"]}
        for i, c in enumerate(merged)
    ]
