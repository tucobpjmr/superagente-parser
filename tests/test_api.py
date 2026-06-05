"""
Test di integrazione per gli endpoint FastAPI.
Usa TestClient (sync) + mock per OCR e OpenAI.
"""

import io
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from main import app, _rate_store

client = TestClient(app, raise_server_exceptions=False)

# TestClient usa "testclient" come client host nello scope ASGI
_TEST_IP = "testclient"


@pytest.fixture(autouse=True)
def reset_rate_store():
    _rate_store.clear()
    yield
    _rate_store.clear()


def _upload(filename="doc.txt", content=b"ciao", content_type="text/plain",
            modulo="contratti", categoria="legale", documento_id=None,
            headers=None):
    data = {"modulo": modulo, "categoria": categoria}
    if documento_id:
        data["documento_id"] = documento_id
    return client.post(
        "/parse",
        files=[("file", (filename, io.BytesIO(content), content_type))],
        data=data,
        headers=headers or {},
    )


def _mock_ocr(text="# Titolo\n" + "parola " * 100):
    result = MagicMock()
    result.text_content = text
    return patch.object(main.md_converter, "convert_stream", return_value=result)


def _mock_embeddings(dim=1536):
    async def _fake(texts):
        return [[0.1] * dim for _ in texts]
    return patch("main.generate_embeddings_batch", side_effect=_fake)


# ── /health ─────────────────────────────────────────────────────────────────

def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["embedding_dim"] == main.EMBEDDING_DIM


# ── /ready ──────────────────────────────────────────────────────────────────

def test_ready_no_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    resp = client.get("/ready")
    assert resp.status_code == 503
    body = resp.json()["detail"]
    assert body["checks"]["openai_key"] is False


def test_ready_with_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


# ── Rate limiting ────────────────────────────────────────────────────────────

def test_rate_limit_triggers_429():
    now = time.monotonic()
    _rate_store[_TEST_IP] = [now for _ in range(main._RATE_LIMIT)]
    resp = _upload()
    assert resp.status_code == 429
    assert "Troppe richieste" in resp.json()["detail"]


def test_rate_limit_resets_after_window():
    # Timestamp scaduti (più vecchi della finestra)
    old = time.monotonic() - main._RATE_WINDOW - 1
    _rate_store[_TEST_IP] = [old for _ in range(main._RATE_LIMIT)]
    # La prossima richiesta deve passare (non 429)
    with _mock_ocr(), _mock_embeddings():
        resp = _upload()
    assert resp.status_code != 429


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_auth_required_without_header(monkeypatch):
    monkeypatch.setattr(main, "PARSER_SHARED_SECRET", "s3cr3t")
    resp = _upload()
    assert resp.status_code == 401


def test_auth_wrong_token(monkeypatch):
    monkeypatch.setattr(main, "PARSER_SHARED_SECRET", "s3cr3t")
    resp = _upload(headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_auth_valid_token(monkeypatch):
    monkeypatch.setattr(main, "PARSER_SHARED_SECRET", "s3cr3t")
    with _mock_ocr(), _mock_embeddings():
        resp = _upload(headers={"Authorization": "Bearer s3cr3t"})
    assert resp.status_code == 200


# ── Validazione form ─────────────────────────────────────────────────────────

def test_form_empty_modulo():
    # FastAPI ritorna 422 per required Form field con stringa vuota (validation pre-handler)
    resp = _upload(modulo="")
    assert resp.status_code in (400, 422)


def test_form_whitespace_modulo():
    # Spazi soli: supera la validazione di FastAPI ma viene rifiutato dal nostro check → 400
    resp = _upload(modulo="   ")
    assert resp.status_code == 400
    assert "modulo" in resp.json()["detail"]


def test_form_long_modulo():
    resp = _upload(modulo="x" * (main._FIELD_MAX + 1))
    assert resp.status_code == 400


def test_form_empty_categoria():
    resp = _upload(categoria="")
    assert resp.status_code in (400, 422)


def test_form_long_documento_id():
    resp = _upload(documento_id="d" * (main._FIELD_MAX + 1))
    assert resp.status_code == 400
    assert "documento_id" in resp.json()["detail"]


# ── Estensione e MIME ────────────────────────────────────────────────────────

def test_unsupported_extension():
    resp = _upload(filename="evil.exe", content_type="application/octet-stream")
    assert resp.status_code == 400
    assert ".exe" in resp.json()["detail"]


def test_no_extension():
    resp = _upload(filename="noext", content_type="application/octet-stream")
    assert resp.status_code == 400


def test_mime_mismatch_rejected():
    resp = _upload(filename="doc.pdf", content_type="image/png")
    assert resp.status_code == 400
    assert "MIME" in resp.json()["detail"]


def test_mime_octet_stream_accepted():
    """application/octet-stream deve essere accettato per qualsiasi estensione valida."""
    with _mock_ocr(), _mock_embeddings():
        resp = _upload(filename="doc.txt", content_type="application/octet-stream")
    assert resp.status_code == 200


def test_mime_absent_accepted():
    """Nessun content_type → accettato senza controllo MIME."""
    with _mock_ocr(), _mock_embeddings():
        resp = _upload(filename="doc.txt", content_type="")
    assert resp.status_code == 200


# ── Dimensione file ──────────────────────────────────────────────────────────

def test_file_too_large_rejected():
    big = b"x" * (main.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    resp = _upload(filename="big.txt", content=big, content_type="text/plain")
    assert resp.status_code == 413


# ── Sanitizzazione filename ──────────────────────────────────────────────────

def test_path_traversal_in_filename_stripped():
    with _mock_ocr(), _mock_embeddings():
        resp = _upload(filename="../../etc/passwd.txt")
    assert resp.status_code == 200
    assert "passwd.txt" in resp.json()["metadata"]["file"]
    assert ".." not in resp.json()["metadata"]["file"]


# ── Parse success ────────────────────────────────────────────────────────────

def test_parse_success_full_response():
    with _mock_ocr("# Titolo\n" + "parola " * 100), _mock_embeddings():
        resp = _upload(
            filename="report.txt",
            modulo="contratti",
            categoria="legale",
            documento_id="doc-123",
        )
    assert resp.status_code == 200
    body = resp.json()

    meta = body["metadata"]
    assert meta["modulo"] == "contratti"
    assert meta["categoria"] == "legale"
    assert meta["n_chunks"] >= 1
    assert meta["file"] == "report.txt"

    chunks = body["chunks"]
    assert len(chunks) == meta["n_chunks"]
    assert chunks[0]["chunk_index"] == 0
    assert len(chunks[0]["embedding"]) == 1536
    assert chunks[0]["modulo"] == "contratti"
    assert chunks[0]["documento_id"] == "doc-123"


def test_parse_empty_ocr_result_returns_422():
    with _mock_ocr(""):
        resp = _upload()
    assert resp.status_code == 422
    assert "testo" in resp.json()["detail"].lower()


def test_parse_ocr_exception_returns_422():
    with patch.object(main.md_converter, "convert_stream", side_effect=ValueError("corrupt")):
        resp = _upload()
    assert resp.status_code == 422
    assert "corrupt" in resp.json()["detail"]
