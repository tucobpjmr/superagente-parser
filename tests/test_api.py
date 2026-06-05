"""
Test suite per main.py — endpoint /health, /ready, /parse.
All external calls (OpenAI, Markitdown) are mocked.
"""

import io
import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _reload_main(**env):
    for k, v in env.items():
        os.environ[k] = v

    import main
    importlib.reload(main)

    for k in env:
        os.environ.pop(k, None)

    return TestClient(main.app), main


def _upload(client, filename="doc.pdf", content=b"fake", headers=None, **form):
    files = {"file": (filename, io.BytesIO(content), "application/octet-stream")}
    data = {"modulo": "fiscalita", "categoria": "normativa", **form}
    return client.post("/parse", files=files, data=data, headers=headers or {})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PARSER_SHARED_SECRET", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)


# ---------- Health / Ready ----------

class TestHealthReady:
    def test_health(self):
        client, _ = _reload_main()
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert "embedding_dim" in r.json()

    def test_ready(self):
        client, _ = _reload_main()
        r = client.get("/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------- Auth ----------

class TestAuth:
    def test_no_secret_allows_request(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content="parola " * 60)
            emb.return_value = [[0.1] * 1536]
            r = _upload(client)
        assert r.status_code == 200

    def test_secret_set_no_header_401(self):
        client, _ = _reload_main(PARSER_SHARED_SECRET="s3cret")
        r = _upload(client)
        assert r.status_code == 401

    def test_secret_set_wrong_bearer_401(self):
        client, _ = _reload_main(PARSER_SHARED_SECRET="s3cret")
        r = _upload(client, headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_secret_set_correct_bearer_200(self):
        client, _ = _reload_main(PARSER_SHARED_SECRET="s3cret")
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content="parola " * 60)
            emb.return_value = [[0.1] * 1536]
            r = _upload(client, headers={"Authorization": "Bearer s3cret"})
        assert r.status_code == 200


# ---------- Validation ----------

class TestValidation:
    def test_unsupported_extension_400(self):
        client, _ = _reload_main()
        r = _upload(client, filename="malware.exe")
        assert r.status_code == 400
        assert "non supportata" in r.json()["detail"]

    def test_file_too_large_413(self):
        client, _ = _reload_main(MAX_UPLOAD_MB="1")
        big = b"x" * (2 * 1024 * 1024)
        r = _upload(client, content=big)
        assert r.status_code == 413
        assert "troppo grande" in r.json()["detail"]

    def test_no_filename_rejected(self):
        client, _ = _reload_main()
        files = {"file": ("", io.BytesIO(b"data"), "application/octet-stream")}
        data = {"modulo": "m", "categoria": "c"}
        r = client.post("/parse", files=files, data=data)
        assert r.status_code in (400, 422)


# ---------- Markitdown errors ----------

class TestMarkitdown:
    def test_convert_exception_422(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md:
            md.convert_stream.side_effect = RuntimeError("boom")
            r = _upload(client)
        assert r.status_code == 422
        assert "Errore parsing" in r.json()["detail"]

    def test_empty_text_422(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md:
            md.convert_stream.return_value = MagicMock(text_content="")
            r = _upload(client)
        assert r.status_code == 422
        assert "Nessun testo estratto" in r.json()["detail"]

    def test_none_text_422(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md:
            md.convert_stream.return_value = MagicMock(text_content=None)
            r = _upload(client)
        assert r.status_code == 422
        assert "Nessun testo estratto" in r.json()["detail"]


# ---------- Embedding error ----------

class TestEmbeddingError:
    def test_openai_failure_502(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content="parola " * 100)
            emb.side_effect = RuntimeError("OpenAI down")
            r = _upload(client)
        assert r.status_code == 502
        assert "Errore OpenAI" in r.json()["detail"]


# ---------- Happy path ----------

class TestHappyPath:
    def test_full_parse_response(self):
        client, _ = _reload_main()
        text = "# Titolo\n" + "parola " * 100
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content=text)
            emb.return_value = [[0.1] * 1536]
            r = _upload(client, filename="test.pdf", documento_id="uuid-123")

        assert r.status_code == 200
        body = r.json()
        assert "markdown" in body
        assert "chunks" in body
        assert "metadata" in body
        chunk = body["chunks"][0]
        assert chunk["modulo"] == "fiscalita"
        assert chunk["categoria"] == "normativa"
        assert chunk["documento_id"] == "uuid-123"
        assert chunk["chunk_index"] == 0
        assert len(chunk["embedding"]) == 1536
        meta = body["metadata"]
        assert meta["file"] == "test.pdf"
        assert meta["n_chunks"] == len(body["chunks"])
        assert meta["embedding_model"] == "text-embedding-3-small"

    def test_documento_id_optional(self):
        client, _ = _reload_main()
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content="parola " * 60)
            emb.return_value = [[0.1] * 1536]
            r = _upload(client)

        assert r.status_code == 200
        assert r.json()["chunks"][0]["documento_id"] is None

    def test_multiple_chunks_get_embeddings(self):
        client, _ = _reload_main()
        text = "# Titolo\n" + "parola " * 1500
        with patch("main.md_converter") as md, \
             patch("main.generate_embeddings_batch", new_callable=AsyncMock) as emb:
            md.convert_stream.return_value = MagicMock(text_content=text)
            emb.return_value = [[0.1] * 1536] * 4
            r = _upload(client)

        assert r.status_code == 200
        body = r.json()
        assert len(body["chunks"]) > 1
        for i, ch in enumerate(body["chunks"]):
            assert ch["chunk_index"] == i
