"""
Test E2E con un vero PDF (eval/fixtures/sample.pdf): valida l'intera pipeline
estrazione reale (pdfminer via markitdown) → chunking → embedding mockato.
A differenza di test_api.py, qui l'OCR/estrazione NON è mockata.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app, _rate_store

client = TestClient(app, raise_server_exceptions=False)

FIXTURE = Path(__file__).parent.parent / "eval" / "fixtures" / "sample.pdf"


@pytest.fixture(autouse=True)
def reset_rate_store():
    _rate_store.clear()
    yield
    _rate_store.clear()


def _mock_embeddings(dim=1536):
    async def _fake(texts):
        return [[0.0] * dim for _ in texts]
    return patch("main.generate_embeddings_batch", side_effect=_fake)


def _mock_summary():
    async def _fake(markdown):
        return None
    return patch("main.generate_document_summary", side_effect=_fake)


def _mock_classify():
    async def _fake(texts, hint=None):
        return [{"discipline": [], "tags": [], "entities": None} for _ in texts]
    return patch("main.classify_chunks", side_effect=_fake)


def test_parse_real_pdf_end_to_end():
    pdf_bytes = FIXTURE.read_bytes()
    with _mock_embeddings(), _mock_summary(), _mock_classify():
        r = client.post(
            "/parse",
            files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
            data={"modulo": "turismo", "categoria": "test"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    # Il testo del PDF è stato estratto davvero (niente mock di markitdown)
    assert "SuperAgente Parser" in body["markdown"]
    assert "crociera" in body["markdown"]
    assert body["metadata"]["n_chunks"] >= 1
    chunk = body["chunks"][0]
    assert chunk["embedding"] == [0.0] * 1536
    assert chunk["modulo"] == "turismo"
    # Fallback enrichment: il modulo resta la prima (unica) disciplina
    assert chunk["discipline"][0] == "turismo"
