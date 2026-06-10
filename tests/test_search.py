"""
Test della pipeline /search: decomposizione, fan-out, RRF, rerank.
Mocka OpenAI e Supabase RPC; non richiede env reale.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
import search as search_mod
from main import app, _rate_store


client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_rate_store():
    _rate_store.clear()
    yield
    _rate_store.clear()


@pytest.fixture(autouse=True)
def set_supabase_env(monkeypatch):
    monkeypatch.setattr(search_mod, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(search_mod, "SUPABASE_SERVICE_KEY", "test-key")


# ---------------------------------------------------------------------------
# Unit: rrf_fuse
# ---------------------------------------------------------------------------

def test_rrf_fuse_single_list():
    r = [{"id": "a", "contenuto": "x"}, {"id": "b", "contenuto": "y"}]
    out = search_mod.rrf_fuse([r])
    assert [c["id"] for c in out] == ["a", "b"]
    assert out[0]["rrf_score"] > out[1]["rrf_score"]


def test_rrf_fuse_combines_lists():
    l1 = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    l2 = [{"id": "b"}, {"id": "a"}, {"id": "d"}]
    out = search_mod.rrf_fuse([l1, l2], k=60)
    ids = [c["id"] for c in out]
    # 'a' e 'b' presenti in entrambe → score più alto di 'c' e 'd'
    assert set(ids[:2]) == {"a", "b"}
    assert "c" in ids and "d" in ids


def test_rrf_fuse_skips_missing_id():
    out = search_mod.rrf_fuse([[{"contenuto": "no id"}, {"id": "a"}]])
    assert [c["id"] for c in out] == ["a"]


# ---------------------------------------------------------------------------
# Unit: decompose_query
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_decompose_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = await search_mod.decompose_query("qualcosa")
    assert out == [{"testo": "qualcosa", "testo_en": "", "discipline": []}]


@pytest.mark.asyncio
async def test_decompose_disabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr(search_mod, "SEARCH_DECOMPOSE", False)
    out = await search_mod.decompose_query("x")
    assert out == [{"testo": "x", "testo_en": "", "discipline": []}]


@pytest.mark.asyncio
async def test_decompose_llm_error_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    mock_client = MagicMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(search_mod, "get_openai_client", return_value=mock_client):
        out = await search_mod.decompose_query("x")
    assert out == [{"testo": "x", "testo_en": "", "discipline": []}]


@pytest.mark.asyncio
async def test_decompose_parses_and_filters_taxonomy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    payload = json.dumps({
        "sotto_domande": [
            {
                "testo": "rimborso contrattuale annullamento",
                "testo_en": "contractual refund cancellation",
                "discipline": ["contrattualistica", "DISCIPLINA-INVENTATA"],
            },
            {
                "testo": "copertura polizza malattia",
                "testo_en": "illness insurance coverage",
                "discipline": ["assicurazioni"],
            },
        ]
    })
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=payload))]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
    with patch.object(search_mod, "get_openai_client", return_value=mock_client):
        out = await search_mod.decompose_query("Annullamento crociera per malattia?")
    assert len(out) == 2
    # Le discipline fuori tassonomia vengono scartate
    assert out[0]["discipline"] == ["contrattualistica"]
    assert out[1]["discipline"] == ["assicurazioni"]
    assert out[0]["testo_en"] == "contractual refund cancellation"


# ---------------------------------------------------------------------------
# E2E endpoint /search con mock completi
# ---------------------------------------------------------------------------

def _mock_match_chunks_response(ids):
    return [
        {
            "id": cid,
            "documento_id": f"doc-{cid}",
            "chunk_index": i,
            "contenuto": f"contenuto {cid}",
            "heading": "H1 > H2",
            "discipline": ["contrattualistica"],
            "categoria": "test",
            "score": 1.0 - i * 0.1,
        }
        for i, cid in enumerate(ids)
    ]


def _patch_pipeline(monkeypatch, match_side_effect=None):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    async def fake_decompose(domanda, max_n=4):
        return [
            {"testo": "sub1", "testo_en": "sub1 en", "discipline": ["contrattualistica"]},
            {"testo": "sub2", "testo_en": "", "discipline": ["assicurazioni"]},
        ]

    async def fake_embed(texts):
        return [[0.0] * 1536 for _ in texts]

    async def fake_match(http, emb, qtext, discipline, match_count, rrf_k=50):
        if match_side_effect:
            raise match_side_effect
        if qtext == "sub1":
            return _mock_match_chunks_response(["a", "b", "c"])
        return _mock_match_chunks_response(["b", "d"])

    async def fake_rerank(domanda, candidates, top_k):
        return candidates[:top_k]

    async def fake_doc_names(http, ids):
        return {f"doc-{c}": f"file-{c}.pdf" for c in ["a", "b", "c", "d"]}

    monkeypatch.setattr(search_mod, "decompose_query", fake_decompose)
    monkeypatch.setattr(search_mod, "generate_embeddings_batch", fake_embed)
    monkeypatch.setattr(search_mod, "call_match_chunks", fake_match)
    monkeypatch.setattr(search_mod, "rerank_llm", fake_rerank)
    monkeypatch.setattr(search_mod, "fetch_document_names", fake_doc_names)


def test_search_endpoint_full_pipeline(monkeypatch):
    _patch_pipeline(monkeypatch)
    r = client.post("/search", json={"domanda": "Domanda complessa", "top_k": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["domanda"] == "Domanda complessa"
    assert len(body["sotto_domande"]) == 2
    assert body["n_candidati"] == 4  # a, b, c, d dedupe
    assert len(body["risultati"]) == 3
    assert "duration_s" in body
    # Ogni risultato porta sotto_domanda e nome_file
    for res in body["risultati"]:
        assert res["sotto_domanda"] in ("sub1", "sub2")
        assert res["nome_file"].endswith(".pdf")


def test_search_caller_discipline_boost(monkeypatch):
    """Le discipline passate dal chiamante arrivano a match_chunks come boost."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    captured = []

    async def fake_decompose(domanda, max_n=4):
        return [{"testo": "sub1", "testo_en": "", "discipline": ["turismo"]}]

    async def fake_embed(texts):
        return [[0.0] * 1536 for _ in texts]

    async def fake_match(http, emb, qtext, discipline, match_count, rrf_k=50):
        captured.append(discipline)
        return _mock_match_chunks_response(["a"])

    async def fake_doc_names(http, ids):
        return {}

    monkeypatch.setattr(search_mod, "decompose_query", fake_decompose)
    monkeypatch.setattr(search_mod, "generate_embeddings_batch", fake_embed)
    monkeypatch.setattr(search_mod, "call_match_chunks", fake_match)
    monkeypatch.setattr(search_mod, "fetch_document_names", fake_doc_names)

    r = client.post(
        "/search",
        json={"domanda": "x", "discipline": ["fiscalita"], "rerank": False},
    )
    assert r.status_code == 200, r.text
    assert captured == [["turismo", "fiscalita"]]


def test_search_missing_supabase_config(monkeypatch):
    monkeypatch.setattr(search_mod, "SUPABASE_URL", "")
    monkeypatch.setattr(search_mod, "SUPABASE_SERVICE_KEY", "")
    r = client.post("/search", json={"domanda": "x"})
    assert r.status_code == 503
    assert "SUPABASE" in r.json()["detail"]


def test_search_validation_empty_domanda():
    r = client.post("/search", json={"domanda": ""})
    assert r.status_code == 422  # pydantic min_length


def test_search_auth_required(monkeypatch):
    monkeypatch.setattr(main, "PARSER_SHARED_SECRET", "secret")
    r = client.post("/search", json={"domanda": "x"})
    assert r.status_code == 401
    r = client.post(
        "/search", json={"domanda": "x"}, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401


def test_search_all_subqueries_fail(monkeypatch):
    _patch_pipeline(monkeypatch, match_side_effect=RuntimeError("supabase down"))
    r = client.post("/search", json={"domanda": "ciao"})
    assert r.status_code == 503
    assert "fallite" in r.json()["detail"].lower()
