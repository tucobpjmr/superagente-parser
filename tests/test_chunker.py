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


def test_merge_tiny_tail_absorbed_into_previous():
    """Bug fix: l'ultimo chunk piccolo non deve restare orfano ma riassorbirsi nel precedente."""
    # 3 sezioni: big, big, tiny — la tiny deve finire nel secondo chunk
    big = "parola " * 200   # 200 parole, sopra min_chunk_words=50
    tiny = "fine doc"       # 2 parole, sotto min_chunk_words=50
    md = f"# A\n{big}\n\n# B\n{big}\n\n# C\n{tiny}"
    chunks = chunk_markdown(md, chunk_size=500, min_chunk_words=50)
    # Nessun chunk deve avere solo 2 parole
    for c in chunks:
        assert _word_count(c["contenuto"]) >= 50, (
            f"chunk orfano trovato ({_word_count(c['contenuto'])} parole): {c['contenuto']!r}"
        )
    # Il contenuto del chunk finale deve contenere "fine doc"
    assert any("fine doc" in c["contenuto"] for c in chunks)


def test_merge_does_not_mutate_input():
    """Bug fix: il merge non deve mutare i dict originali di raw_chunks."""
    md = "# A\nuno due tre\n\n# B\nquattro cinque sei"
    # chunk_markdown è puro: chiamarlo due volte deve dare lo stesso risultato
    result1 = chunk_markdown(md, chunk_size=500, min_chunk_words=1)
    result2 = chunk_markdown(md, chunk_size=500, min_chunk_words=1)
    assert [c["contenuto"] for c in result1] == [c["contenuto"] for c in result2]


def test_merge_multiple_tiny_chunks_accumulate():
    """Più chunk piccoli consecutivi devono accumularsi fino a raggiungere min_chunk_words."""
    # 5 sezioni da 20 parole ciascuna, min=50: devono unirsi almeno a 3 sezioni per volta
    section = "parola " * 20
    md = "\n\n".join(f"# Sez{i}\n{section}" for i in range(5))
    chunks = chunk_markdown(md, chunk_size=500, min_chunk_words=50)
    for c in chunks[:-1]:  # l'ultimo potrebbe essere assorbito dal precedente
        assert _word_count(c["contenuto"]) >= 50
