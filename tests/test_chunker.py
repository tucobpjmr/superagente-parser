"""
Test base per chunker.
Esegui con: pytest tests/test_chunker.py
"""

from chunker import chunk_markdown, _split_by_headings, _word_count


def test_split_by_headings_no_headings():
    md = "Solo testo senza heading. Diverse frasi."
    secs = _split_by_headings(md)
    assert len(secs) == 1
    assert secs[0]["heading"] is None


def test_split_by_headings_multiple():
    md = """# Intro
Testo intro.

## Sezione A
Contenuto A.

## Sezione B
Contenuto B."""
    secs = _split_by_headings(md)
    assert len(secs) == 3
    assert secs[0]["heading"] == "Intro"
    assert secs[1]["heading"] == "Sezione A"
    assert secs[2]["heading"] == "Sezione B"


def test_chunk_small_doc_returns_single_chunk():
    md = "# Titolo\n" + ("parola " * 100)
    chunks = chunk_markdown(md, chunk_size=500)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0


def test_chunk_large_doc_splits():
    md = "# Titolo\n" + ("parola " * 1500)
    chunks = chunk_markdown(md, chunk_size=500, overlap=50)
    assert len(chunks) >= 3
    # Verifica indici consecutivi
    for i, c in enumerate(chunks):
        assert c["chunk_index"] == i


def test_chunk_preserves_heading():
    md = """# Capitolo 1
""" + ("parola " * 200) + """

# Capitolo 2
""" + ("parola " * 200)
    chunks = chunk_markdown(md, chunk_size=500)
    headings = [c["heading"] for c in chunks]
    assert "Capitolo 1" in headings
    assert "Capitolo 2" in headings


def test_word_count():
    assert _word_count("una due tre") == 3
    assert _word_count("") == 0
