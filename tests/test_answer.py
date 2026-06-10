"""Test /answer: build_context/citations + endpoint con mock di search+LLM."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import answer as answer_mod
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
# Unit
# ---------------------------------------------------------------------------

def test_build_context_groups_by_discipline():
    chunks = [
        {"id": "a", "discipline": ["contrattualistica"], "heading": "Art. 12",
         "nome_file": "x.pdf", "contenuto": "testo A"},
        {"id": "b", "discipline": ["assicurazioni"], "heading": "Polizza",
         "nome_file": "y.pdf", "contenuto": "testo B"},
        {"id": "c", "discipline": ["contrattualistica"], "heading": "Art. 13",
         "nome_file": "x.pdf", "contenuto": "testo C"},
    ]
    ctx = answer_mod._build_context(chunks)
    assert "Disciplina: contrattualistica" in ctx
    assert "Disciplina: assicurazioni" in ctx
    # Indici 1-based allineati all'ordine di input
    assert "[1]" in ctx and "[2]" in ctx and "[3]" in ctx
    # I chunk della stessa disciplina sono raggruppati ma i loro indici
    # restano quelli originali
    contr_section = ctx.split("Disciplina: contrattualistica")[1].split("Disciplina:")[0]
    assert "[1]" in contr_section and "[3]" in contr_section


def test_build_citations_shape():
    chunks = [
        {"id": "u1", "documento_id": "d1", "discipline": ["fiscalita", "turismo"],
         "heading": "Sez. 1", "nome_file": "f.pdf"},
        {"id": "u2", "documento_id": "d2", "discipline": [],
         "heading": None, "nome_file": None},
    ]
    out = answer_mod._build_citations(chunks)
    assert out[0]["n"] == 1
    assert out[0]["chunk_id"] == "u1"
    assert out[0]["disciplina"] == "fiscalita"
    assert out[0]["discipline"] == ["fiscalita", "turismo"]
    assert out[1]["n"] == 2
    assert out[1]["disciplina"] is None


@pytest.mark.asyncio
async def test_synthesize_empty_chunks_no_llm_call():
    out = await answer_mod.synthesize_answer("domanda", [])
    assert "non ho trovato fonti" in out.lower()


@pytest.mark.asyncio
async def test_synthesize_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await answer_mod.synthesize_answer("d", [{"id": "a", "contenuto": "x", "discipline": []}])


# ---------------------------------------------------------------------------
# Endpoint /answer
# ---------------------------------------------------------------------------

def _mock_search_result():
    return {
        "domanda": "Annullamento crociera per malattia?",
        "sotto_domande": [
            {"testo": "rimborso", "testo_en": "", "discipline": ["contrattualistica"]},
        ],
        "n_candidati": 2,
        "risultati": [
            {"id": "u1", "documento_id": "d1", "discipline": ["contrattualistica"],
             "heading": "Art. 12", "nome_file": "contratto.pdf",
             "contenuto": "Il viaggiatore ha diritto al rimborso integrale...",
             "score": 0.9, "rrf_score": 0.05, "sotto_domanda": "rimborso"},
            {"id": "u2", "documento_id": "d2", "discipline": ["assicurazioni"],
             "heading": "Polizza", "nome_file": "polizza.pdf",
             "contenuto": "La polizza copre l'annullamento per malattia documentata.",
             "score": 0.8, "rrf_score": 0.04, "sotto_domanda": "rimborso"},
        ],
        "errors": None,
    }


def test_answer_endpoint_full(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    async def fake_search(domanda, top_k=None, discipline=None, rerank=None):
        return _mock_search_result()

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(
        content="Il viaggiatore ha diritto al rimborso [1]; la polizza copre [2]."
    ))]
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    monkeypatch.setattr(answer_mod, "search_pipeline", fake_search)
    with patch.object(answer_mod, "get_openai_client", return_value=mock_client):
        r = client.post("/answer", json={"domanda": "Annullamento per malattia?"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert "[1]" in body["risposta"] and "[2]" in body["risposta"]
    assert len(body["citazioni"]) == 2
    assert body["citazioni"][0]["n"] == 1
    assert body["citazioni"][0]["chunk_id"] == "u1"
    assert body["citazioni"][0]["disciplina"] == "contrattualistica"
    assert body["citazioni"][1]["disciplina"] == "assicurazioni"
    assert body["retrieval"]["n_candidati"] == 2
    assert "duration_s" in body


def test_answer_validation_empty():
    r = client.post("/answer", json={"domanda": ""})
    assert r.status_code == 422


def test_answer_auth_required(monkeypatch):
    monkeypatch.setattr(main, "PARSER_SHARED_SECRET", "secret")
    r = client.post("/answer", json={"domanda": "x"})
    assert r.status_code == 401


def test_answer_no_results_returns_explicit_message(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")

    async def fake_search(domanda, top_k=None, discipline=None, rerank=None):
        return {
            "domanda": domanda, "sotto_domande": [], "n_candidati": 0,
            "risultati": [], "errors": None,
        }

    monkeypatch.setattr(answer_mod, "search_pipeline", fake_search)
    r = client.post("/answer", json={"domanda": "qualcosa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["citazioni"] == []
    assert "non ho trovato fonti" in body["risposta"].lower()


def test_answer_search_failure_propagates_503(monkeypatch):
    async def fake_search(domanda, top_k=None, discipline=None, rerank=None):
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY non impostate")

    monkeypatch.setattr(answer_mod, "search_pipeline", fake_search)
    r = client.post("/answer", json={"domanda": "x"})
    assert r.status_code == 503
    assert "SUPABASE" in r.json()["detail"]
