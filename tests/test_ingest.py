"""
Test per scripts/ingest.py.

Nessuna rete reale: un httpx.MockTransport simula sia il parser (/parse) sia
Supabase (PostgREST /rest/v1/documenti e /document_chunks). Il MockTransport
esercita la vera costruzione delle richieste httpx (multipart, params, json),
quindi copre anche il mapping delle colonne e la serializzazione dell'embedding.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

# scripts/ non è un package: aggiungilo al path per importare ingest.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ingest  # noqa: E402


# ---------------------------------------------------------------------------
# Backend finto: parser + Supabase in memoria
# ---------------------------------------------------------------------------

class FakeBackend:
    def __init__(self, parse_response, *, chunks_status=201):
        self.parse_response = parse_response
        self.chunks_status = chunks_status
        self.documenti = {}          # id -> row
        self.chunks = []             # righe document_chunks
        self._next = 1
        self.calls = []              # (method, path) di ogni richiesta

    def seed_document(self, content_hash):
        did = f"doc-seed-{self._next}"
        self._next += 1
        self.documenti[did] = {"content_hash": content_hash}
        return did

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))

        if path.endswith("/parse"):
            return httpx.Response(200, json=self.parse_response)

        if path.endswith("/rest/v1/documenti"):
            if request.method == "GET":
                ch = request.url.params.get("content_hash", "").replace("eq.", "")
                rows = [{"id": did} for did, r in self.documenti.items()
                        if r.get("content_hash") == ch]
                return httpx.Response(200, json=rows[:1])
            if request.method == "POST":
                row = json.loads(request.content)
                did = f"doc-{self._next}"
                self._next += 1
                self.documenti[did] = row
                return httpx.Response(201, json=[{**row, "id": did}])
            if request.method == "DELETE":
                did = request.url.params.get("id", "").replace("eq.", "")
                self.documenti.pop(did, None)
                self.chunks = [c for c in self.chunks if c["documento_id"] != did]
                return httpx.Response(204)

        if path.endswith("/rest/v1/document_chunks") and request.method == "POST":
            if self.chunks_status >= 400:
                return httpx.Response(self.chunks_status, json={"message": "boom"})
            self.chunks.extend(json.loads(request.content))
            return httpx.Response(201)

        return httpx.Response(404, json={"message": f"unhandled {request.method} {path}"})


def make_parse_response(content_hash, n_chunks=2):
    chunks = []
    for i in range(n_chunks):
        chunks.append({
            "chunk_index": i,
            "contenuto": f"contenuto del chunk {i}",
            "embedding": [0.1 * i, -0.2 * i, 0.3],
            "heading": f"H{i}",
            "heading_path": f"Titolo > Sezione {i}",
            "modulo": "contrattualistica",
            "discipline": ["contrattualistica", "normativa-ue"] if i == 0 else ["contrattualistica"],
            "tags": ["rimborso"] if i == 0 else [],
            "entities": {"riferimenti_normativi": ["art. 41 CdT"]} if i == 0 else None,
            "categoria": "normativa",
            "documento_id": None,
        })
    return {
        "markdown": "# Titolo\n\ntesto...",
        "chunks": chunks,
        "metadata": {
            "file": "documento.pdf",
            "size_mb": 0.01,
            "modulo": "contrattualistica",
            "categoria": "normativa",
            "n_chunks": n_chunks,
            "riassunto_documento": "Un riassunto di prova.",
            "embedding_model": "text-embedding-3-small",
            "embedding_dim": 1536,
            "content_hash": content_hash,
            "cached": False,
        },
    }


@pytest.fixture
def cfg():
    return ingest.Config(
        parser_url="https://parser.example",
        secret="s3cr3t",
        supabase_url="https://proj.supabase.co",
        supabase_key="service-key",
    )


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "documento.pdf"
    p.write_bytes(b"%PDF-1.4 finto contenuto per hashing")
    return p


def _client(backend):
    return httpx.Client(transport=httpx.MockTransport(backend.handler))


# ---------------------------------------------------------------------------
# Unit
# ---------------------------------------------------------------------------

def test_vector_literal_format():
    assert ingest._vector_literal([0.1, -0.2, 0.3]) == "[0.1,-0.2,0.3]"


def test_vector_literal_casts_ints():
    assert ingest._vector_literal([1, 2]) == "[1.0,2.0]"


def test_collect_files_filters_and_dedups(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.txt").write_bytes(b"x")
    (tmp_path / "skip.exe").write_bytes(b"x")
    explicit = str(tmp_path / "a.pdf")
    files = ingest.collect_files([explicit], str(tmp_path))
    names = sorted(p.name for p in files)
    assert names == ["a.pdf", "b.txt"]  # .exe escluso, a.pdf non duplicato


def test_collect_files_missing_raises():
    with pytest.raises(ingest.IngestError):
        ingest.collect_files(["/non/esiste.pdf"], None)


# ---------------------------------------------------------------------------
# Flusso completo
# ---------------------------------------------------------------------------

def test_ingest_new_document(cfg, pdf):
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash, n_chunks=2))
    with _client(backend) as http:
        res = ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa")

    assert res["status"] == "ingested"
    assert res["n_chunks"] == 2
    # Un documento inserito con le colonne corrette.
    assert len(backend.documenti) == 1
    doc = next(iter(backend.documenti.values()))
    assert doc["nome_file"] == "documento.pdf"
    assert doc["tipo_file"] == "pdf"
    assert doc["content_hash"] == file_hash
    assert doc["n_chunks"] == 2
    assert doc["modulo"] == "contrattualistica"
    assert doc["riassunto"] == "Un riassunto di prova."
    # discipline documento = unione ordinata dei chunk.
    assert doc["discipline"] == ["contrattualistica", "normativa-ue"]
    # Chunk inseriti con embedding come literal pgvector e heading = heading_path.
    assert len(backend.chunks) == 2
    c0 = backend.chunks[0]
    assert c0["embedding"] == "[0.0,-0.0,0.3]"
    assert c0["heading"] == "Titolo > Sezione 0"
    assert c0["tags"] == ["rimborso"]
    assert c0["entities"] == {"riferimenti_normativi": ["art. 41 CdT"]}
    assert all(c["documento_id"] == res["documento_id"] for c in backend.chunks)


def test_idempotent_skip_does_not_call_parse(cfg, pdf):
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash))
    backend.seed_document(file_hash)  # già presente
    with _client(backend) as http:
        res = ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa")

    assert res["status"] == "skipped"
    # /parse non deve essere chiamato, né inserito nulla di nuovo.
    assert not any(p.endswith("/parse") for _, p in backend.calls)
    assert backend.chunks == []


def test_force_reingests_over_existing(cfg, pdf):
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash))
    old_id = backend.seed_document(file_hash)
    with _client(backend) as http:
        res = ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa", force=True)

    assert res["status"] == "ingested"
    assert ("DELETE", "/rest/v1/documenti") in backend.calls
    assert old_id not in backend.documenti          # vecchio rimosso
    assert res["documento_id"] in backend.documenti  # nuovo presente


def test_dry_run_writes_nothing(cfg, pdf):
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash))
    with _client(backend) as http:
        res = ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa", dry_run=True)

    assert res["status"] == "dry-run"
    assert res["n_chunks"] == 2
    assert any(p.endswith("/parse") for _, p in backend.calls)  # /parse chiamato
    assert backend.documenti == {}                              # niente scritture
    assert backend.chunks == []


def test_chunk_failure_rolls_back_document(cfg, pdf):
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash), chunks_status=500)
    with _client(backend) as http:
        with pytest.raises(ingest.IngestError):
            ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa")

    # Il documento creato prima del fallimento chunk deve essere rimosso.
    assert backend.documenti == {}
    assert ("DELETE", "/rest/v1/documenti") in backend.calls


def test_chunks_inserted_in_batches(cfg, pdf, monkeypatch):
    monkeypatch.setattr(ingest, "CHUNK_INSERT_BATCH", 100)
    file_hash = ingest.sha256_file(pdf)
    backend = FakeBackend(make_parse_response(file_hash, n_chunks=250))
    with _client(backend) as http:
        res = ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa")

    assert res["n_chunks"] == 250
    assert len(backend.chunks) == 250
    post_chunk_calls = [c for c in backend.calls if c == ("POST", "/rest/v1/document_chunks")]
    assert len(post_chunk_calls) == 3  # 100 + 100 + 50


def test_parse_http_error_raises(cfg, pdf):
    backend = FakeBackend(make_parse_response("unused"))

    def failing_handler(request):
        if request.url.path.endswith("/parse"):
            return httpx.Response(401, text="Unauthorized")
        return backend.handler(request)

    with httpx.Client(transport=httpx.MockTransport(failing_handler)) as http:
        with pytest.raises(ingest.IngestError, match="401"):
            ingest.ingest_file(http, cfg, pdf, "contrattualistica", "normativa")


# ---------------------------------------------------------------------------
# CLI / config
# ---------------------------------------------------------------------------

def test_build_config_missing_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    args = ingest.parse_args([
        "--parser-url", "https://p", "--modulo", "m", "--categoria", "c",
        "--file", "x.pdf",
    ])
    with pytest.raises(SystemExit):
        ingest.build_config(args)


def test_build_config_reads_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://env.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "env-key")
    monkeypatch.setenv("PARSER_SHARED_SECRET", "env-secret")
    args = ingest.parse_args([
        "--parser-url", "https://p", "--modulo", "m", "--categoria", "c",
    ])
    cfg = ingest.build_config(args)
    assert cfg.supabase_url == "https://env.supabase.co"
    assert cfg.supabase_key == "env-key"
    assert cfg.secret == "env-secret"
