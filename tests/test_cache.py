"""Test cache D4: lookup per content_hash prima di OCR + embedding."""

import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import cache as cache_mod
import main
from main import app, _rate_store


client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_rate_store():
    _rate_store.clear()
    yield
    _rate_store.clear()


# ---------------------------------------------------------------------------
# Unit: _normalize_embedding
# ---------------------------------------------------------------------------

def test_normalize_embedding_list_passthrough():
    assert cache_mod._normalize_embedding([0.1, 0.2]) == [0.1, 0.2]


def test_normalize_embedding_postgrest_string():
    assert cache_mod._normalize_embedding("[0.1,0.2,0.3]") == [0.1, 0.2, 0.3]


def test_normalize_embedding_invalid():
    assert cache_mod._normalize_embedding("not json") is None
    assert cache_mod._normalize_embedding(None) is None
    assert cache_mod._normalize_embedding(42) is None


@pytest.mark.asyncio
async def test_lookup_no_supabase_config(monkeypatch):
    import search as search_mod
    monkeypatch.setattr(search_mod, "SUPABASE_URL", "")
    monkeypatch.setattr(search_mod, "SUPABASE_SERVICE_KEY", "")
    assert await cache_mod.lookup_cached_document("abc") is None


# ---------------------------------------------------------------------------
# Endpoint /parse con cache
# ---------------------------------------------------------------------------

def _upload_kwargs(content=b"contenuto identico"):
    return {
        "files": {"file": ("doc.txt", content, "text/plain")},
        "data": {"modulo": "turismo", "categoria": "test"},
    }


def _cached_doc():
    return {
        "documento_id": "doc-uuid-1",
        "markdown": "# Titolo\ntesto cached",
        "riassunto": "Riassunto cached.",
        "chunks": [
            {
                "chunk_index": 0,
                "contenuto": "testo cached",
                "heading": "Titolo",
                "embedding": [0.5] * 1536,
                "discipline": ["turismo", "contrattualistica"],
                "tags": ["test"],
                "entities": None,
            }
        ],
    }


def test_parse_cache_hit_skips_pipeline(monkeypatch):
    monkeypatch.setattr(main, "PARSE_CACHE", True)
    seen_hash = {}

    async def fake_lookup(content_hash):
        seen_hash["h"] = content_hash
        return _cached_doc()

    async def boom(texts):
        raise AssertionError("embedding NON deve essere chiamato su cache hit")

    with patch("main.lookup_cached_document", side_effect=fake_lookup), \
         patch("main.generate_embeddings_batch", side_effect=boom):
        r = client.post("/parse", **_upload_kwargs())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["cached"] is True
    assert body["metadata"]["documento_id_cache"] == "doc-uuid-1"
    expected_hash = hashlib.sha256(b"contenuto identico").hexdigest()
    assert seen_hash["h"] == expected_hash
    assert body["metadata"]["content_hash"] == expected_hash
    chunk = body["chunks"][0]
    assert chunk["embedding"] == [0.5] * 1536
    # modulo/categoria della richiesta corrente, non del documento cached
    assert chunk["modulo"] == "turismo"
    assert chunk["categoria"] == "test"


def test_parse_cache_miss_runs_pipeline(monkeypatch):
    monkeypatch.setattr(main, "PARSE_CACHE", True)

    async def fake_lookup(content_hash):
        return None

    async def fake_embed(texts):
        return [[0.0] * 1536 for _ in texts]

    async def fake_summary(markdown):
        return None

    async def fake_classify(texts, hint=None):
        return [{"discipline": [], "tags": [], "entities": None} for _ in texts]

    with patch("main.lookup_cached_document", side_effect=fake_lookup), \
         patch("main.generate_embeddings_batch", side_effect=fake_embed), \
         patch("main.generate_document_summary", side_effect=fake_summary), \
         patch("main.classify_chunks", side_effect=fake_classify):
        r = client.post("/parse", **_upload_kwargs())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["metadata"]["cached"] is False
    assert body["metadata"]["content_hash"] == hashlib.sha256(b"contenuto identico").hexdigest()


def test_parse_cache_disabled(monkeypatch):
    monkeypatch.setattr(main, "PARSE_CACHE", False)
    called = {"lookup": False}

    async def fake_lookup(content_hash):
        called["lookup"] = True
        return _cached_doc()

    async def fake_embed(texts):
        return [[0.0] * 1536 for _ in texts]

    async def fake_summary(markdown):
        return None

    async def fake_classify(texts, hint=None):
        return [{"discipline": [], "tags": [], "entities": None} for _ in texts]

    with patch("main.lookup_cached_document", side_effect=fake_lookup), \
         patch("main.generate_embeddings_batch", side_effect=fake_embed), \
         patch("main.generate_document_summary", side_effect=fake_summary), \
         patch("main.classify_chunks", side_effect=fake_classify):
        r = client.post("/parse", **_upload_kwargs())

    assert r.status_code == 200, r.text
    assert called["lookup"] is False
    assert r.json()["metadata"]["cached"] is False
