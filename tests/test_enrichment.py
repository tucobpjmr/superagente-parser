"""
Test per la classificazione multi-disciplina (Fase 1.1/1.2).
Il parsing/validazione della risposta LLM è puro e si testa senza mock;
classify_chunks si testa con il client mockato.
"""

import json
from unittest.mock import patch

import pytest

from enrichment import (
    _parse_classification,
    classify_chunks,
    empty_enrichment,
    get_taxonomy,
    _DEFAULT_TAXONOMY,
)


TAX = list(_DEFAULT_TAXONOMY)


def test_parse_valid_response():
    content = json.dumps({
        "chunks": [
            {
                "i": 0,
                "discipline": ["fiscalita", "assicurazioni"],
                "tags": ["IVA", "Penale"],
                "entities": {"riferimenti_normativi": ["Dlgs 62/2024"], "importi": ["100 EUR"]},
            },
            {"i": 1, "discipline": ["trasporti"], "tags": [], "entities": {}},
        ]
    })
    out = _parse_classification(content, 2, TAX)
    assert out[0]["discipline"] == ["fiscalita", "assicurazioni"]
    assert out[0]["tags"] == ["iva", "penale"]  # normalizzati minuscoli
    assert out[0]["entities"] == {"riferimenti_normativi": ["Dlgs 62/2024"], "importi": ["100 EUR"]}
    assert out[1]["discipline"] == ["trasporti"]
    assert out[1]["entities"] is None  # dict vuoto → None


def test_parse_filters_discipline_outside_taxonomy():
    content = json.dumps({"chunks": [
        {"i": 0, "discipline": ["fiscalita", "astrologia", "FISCALITA"], "tags": [], "entities": None}
    ]})
    out = _parse_classification(content, 1, TAX)
    # "astrologia" non è in tassonomia; "FISCALITA" viene normalizzata
    # minuscola e deduplicata
    assert out[0]["discipline"] == ["fiscalita"]


def test_parse_malformed_json_degrades_to_neutral():
    out = _parse_classification("non è json {", 3, TAX)
    assert out == [empty_enrichment()] * 3


def test_parse_missing_index_uses_position():
    content = json.dumps({"chunks": [
        {"discipline": ["privacy"], "tags": [], "entities": None},
        {"discipline": ["dogane"], "tags": [], "entities": None},
    ]})
    out = _parse_classification(content, 2, TAX)
    assert out[0]["discipline"] == ["privacy"]
    assert out[1]["discipline"] == ["dogane"]


def test_parse_entities_drops_empty_and_non_lists():
    content = json.dumps({"chunks": [
        {"i": 0, "discipline": [], "tags": [],
         "entities": {"importi": [], "date": ["2026-01-01"], "organismi": "ENAC"}}
    ]})
    out = _parse_classification(content, 1, TAX)
    # importi vuoto e organismi non-lista vengono scartati
    assert out[0]["entities"] == {"date": ["2026-01-01"]}


def test_parse_tags_capped_and_cleaned():
    content = json.dumps({"chunks": [
        {"i": 0, "discipline": [], "tags": [f"T{i}" for i in range(20)] + ["  ", 42], "entities": None}
    ]})
    out = _parse_classification(content, 1, TAX)
    assert len(out[0]["tags"]) <= 8
    assert all(isinstance(t, str) and t == t.lower() for t in out[0]["tags"])


def test_taxonomy_env_override(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_TAXONOMY", "Alfa, beta ,,gamma")
    assert get_taxonomy() == ["alfa", "beta", "gamma"]
    monkeypatch.delenv("DISCIPLINE_TAXONOMY")
    assert get_taxonomy() == list(_DEFAULT_TAXONOMY)


@pytest.mark.anyio
async def test_classify_chunks_batch_failure_degrades():
    """Un errore del client (es. API key assente) degrada a risultati neutri."""
    with patch("enrichment.get_client", side_effect=RuntimeError("no key")):
        out = await classify_chunks(["testo uno", "testo due"], hint="fiscalita")
    assert out == [empty_enrichment(), empty_enrichment()]


@pytest.mark.anyio
async def test_classify_chunks_disabled_returns_neutral():
    with patch("enrichment.ENRICHMENT_ENABLED", False):
        out = await classify_chunks(["testo"], hint=None)
    assert out == [empty_enrichment()]


@pytest.fixture
def anyio_backend():
    return "asyncio"
