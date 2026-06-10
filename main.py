"""
Microservizio FastAPI per parsing documenti + embedding.
Endpoint:
  POST /parse  → riceve file, ritorna chunk con embedding
  GET  /health → healthcheck rapido per Railway (liveness)
  GET  /ready  → readiness con verifica dipendenze (timeout 5s)
"""

import asyncio
import io
import json
import logging
import os
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import PurePosixPath, PureWindowsPath
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from markitdown import MarkItDown
from pydantic import BaseModel, Field

from chunker import chunk_markdown, build_contextual_text
from embeddings import generate_embeddings_batch, EMBEDDING_DIM, get_client
from enrichment import generate_document_summary, classify_chunks, empty_enrichment
from search import search_pipeline, MAX_QUERY_CHARS, MAX_TOP_K


# ---------------------------------------------------------------------------
# Logging strutturato (JSON in produzione, testo in sviluppo)
# ---------------------------------------------------------------------------

_LOG_RECORD_BUILTINS = frozenset({
    "name", "msg", "args", "created", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "pathname",
    "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "message", "exc_info", "exc_text", "taskName",
})


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        payload: dict = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }
        for key, val in record.__dict__.items():
            if key not in _LOG_RECORD_BUILTINS and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_logging() -> None:
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "json") == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)


_setup_logging()
log = logging.getLogger("parser")


# ---------------------------------------------------------------------------
# Configurazione (tutti i valori sono sovrascrivibili via env var)
# ---------------------------------------------------------------------------

MAX_UPLOAD_MB   = int(os.getenv("MAX_UPLOAD_MB", "10"))
CHUNK_SIZE      = int(os.getenv("CHUNK_SIZE", "500"))
CHUNK_OVERLAP   = int(os.getenv("CHUNK_OVERLAP", "50"))
OCR_TIMEOUT     = float(os.getenv("OCR_TIMEOUT", "60"))

# Contextual retrieval: arricchisce l'INPUT dell'embedding con titolo, riassunto
# e breadcrumb di sezione. Il testo salvato resta pulito. Disattivabile per
# tornare al comportamento pre-Fase-1 (embedding del solo contenuto del chunk).
CONTEXTUAL_EMBEDDING = os.getenv("CONTEXTUAL_EMBEDDING", "true").lower() in ("1", "true", "yes", "on")
SUMMARY_TIMEOUT = float(os.getenv("SUMMARY_TIMEOUT", "15"))

# Timeout complessivo per la classificazione multi-disciplina (i batch girano
# in parallelo, quindi copre ~1 round-trip LLM più i retry).
CLASSIFY_TIMEOUT = float(os.getenv("CLASSIFY_TIMEOUT", "45"))

PARSER_SHARED_SECRET = os.getenv("PARSER_SHARED_SECRET")

# Rate limiting: sliding window per IP, no threading.Lock necessario perché
# asyncio è single-threaded all'interno di ogni processo worker.
_RATE_LIMIT  = int(os.getenv("PARSE_RATE_LIMIT", "10"))
_RATE_WINDOW = int(os.getenv("PARSE_RATE_WINDOW", "60"))
_rate_store: dict = defaultdict(list)

_FIELD_MAX    = 200
_FILENAME_MAX = 200

# Estensione → set di MIME types accettabili
EXT_TO_MIME = {
    ".pdf":  {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    ".xls":  {"application/vnd.ms-excel"},
    ".pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    ".ppt":  {"application/vnd.ms-powerpoint"},
    ".html": {"text/html"},
    ".htm":  {"text/html"},
    ".txt":  {"text/plain"},
    ".md":   {"text/markdown", "text/plain", "text/x-markdown"},
    ".csv":  {"text/csv", "text/plain", "application/csv"},
    ".json": {"application/json", "text/plain"},
    ".png":  {"image/png"},
    ".jpg":  {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".webp": {"image/webp"},
}
SUPPORTED_EXTS = set(EXT_TO_MIME.keys())
GENERIC_MIMES  = {"application/octet-stream", "binary/octet-stream", ""}

# CORS: parsing robusto, filtra vuoti e fa strip
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]

app = FastAPI(title="SuperAgente Parser", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

md_converter = MarkItDown()

_FILENAME_UNSAFE_RE = re.compile(r"[^\w\-. ]+", re.UNICODE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitize_filename(name: Optional[str]) -> str:
    """
    Rende sicuro un filename per log e risposte:
    - rimuove componenti di path (POSIX e Windows) → solo basename
    - normalizza Unicode (NFKC) e rimuove caratteri di controllo
    - sostituisce caratteri non-alfanumerici (eccetto -, _, ., spazio) con _
    - tronca a _FILENAME_MAX
    """
    if not name:
        return "unnamed"
    base = PurePosixPath(name).name
    base = PureWindowsPath(base).name
    base = unicodedata.normalize("NFKC", base)
    base = "".join(c for c in base if unicodedata.category(c)[0] != "C")
    base = _FILENAME_UNSAFE_RE.sub("_", base).strip(" .")
    if not base:
        return "unnamed"
    if len(base) > _FILENAME_MAX:
        stem, dot, ext = base.rpartition(".")
        if dot and len(ext) <= 10:
            keep = _FILENAME_MAX - len(ext) - 1
            base = stem[:keep] + "." + ext
        else:
            base = base[:_FILENAME_MAX]
    return base


def _is_rate_limited(ip: str) -> bool:
    # Nessun lock: asyncio è cooperativo e single-threaded per processo.
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW
    _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
    if len(_rate_store[ip]) >= _RATE_LIMIT:
        return True
    _rate_store[ip].append(now)
    return False


async def _safe_document_summary(markdown_text: str) -> Optional[str]:
    """
    Genera il riassunto del documento senza mai far fallire l'ingestion:
    timeout stretto + cattura di ogni errore (es. OPENAI_API_KEY assente,
    rate limit). In caso di problema ritorna None e il contesto degrada al
    solo titolo + breadcrumb.
    """
    try:
        return await asyncio.wait_for(
            generate_document_summary(markdown_text), timeout=SUMMARY_TIMEOUT
        )
    except Exception as e:
        log.warning("summary_failed", extra={"error": str(e)})
        return None


async def _safe_classify_chunks(texts: List[str], hint: str) -> List[dict]:
    """
    Classificazione multi-disciplina che non fa mai fallire l'ingestion:
    in caso di timeout o errore ogni chunk degrada al risultato neutro
    (il chiamante vi unisce il modulo legacy come unica disciplina).
    """
    try:
        return await asyncio.wait_for(classify_chunks(texts, hint), timeout=CLASSIFY_TIMEOUT)
    except Exception as e:
        log.warning("classify_failed", extra={"error": str(e), "n_chunks": len(texts)})
        return [empty_enrichment() for _ in texts]


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Legge in chunk da 64KB; rifiuta (413) non appena il totale supera max_bytes."""
    parts: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File troppo grande: supera {max_bytes // (1024 * 1024)}MB durante la lettura",
            )
        parts.append(chunk)
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness: deve restituire <100ms, nessuna I/O esterna."""
    return {"status": "ok", "embedding_dim": EMBEDDING_DIM}


@app.get("/ready")
async def ready():
    """Readiness: verifica dipendenze critiche con timeout stretto."""
    checks = {"openai_key": False, "markitdown": False}
    detail = []

    if os.getenv("OPENAI_API_KEY"):
        checks["openai_key"] = True
        try:
            await asyncio.wait_for(asyncio.to_thread(get_client), timeout=2.0)
        except asyncio.TimeoutError:
            detail.append("openai client init timeout")
            checks["openai_key"] = False
        except Exception as e:
            detail.append(f"openai client error: {e}")
            checks["openai_key"] = False
    else:
        detail.append("OPENAI_API_KEY non impostata")

    checks["markitdown"] = md_converter is not None

    all_ok = all(checks.values())
    payload = {"status": "ready" if all_ok else "not_ready", "checks": checks}
    if detail:
        payload["detail"] = detail
    if not all_ok:
        raise HTTPException(status_code=503, detail=payload)
    return payload


class SearchRequest(BaseModel):
    domanda: str = Field(..., min_length=1, max_length=MAX_QUERY_CHARS)
    top_k: Optional[int] = Field(None, ge=1, le=MAX_TOP_K)
    # Boost morbido (mai filtro rigido), sommato alle discipline individuate
    # dalla decomposizione.
    discipline: Optional[List[str]] = Field(None, max_length=12)
    rerank: Optional[bool] = None


@app.post("/search")
async def search_endpoint(
    request: Request,
    body: SearchRequest,
    authorization: Optional[str] = Header(None),
):
    # --- Rate limiting (condivide il bucket con /parse) ---
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Troppe richieste. Riprova tra un minuto.")

    # --- Auth opzionale ---
    if PARSER_SHARED_SECRET:
        token = (authorization or "").replace("Bearer ", "")
        if token != PARSER_SHARED_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    domanda = body.domanda.strip()
    if not domanda:
        raise HTTPException(status_code=400, detail="domanda non può essere vuota")

    log.info(
        "search_start",
        extra={"domanda_len": len(domanda), "top_k": body.top_k, "discipline": body.discipline},
    )
    t0 = time.monotonic()
    try:
        result = await search_pipeline(
            domanda=domanda,
            top_k=body.top_k,
            discipline=body.discipline,
            rerank=body.rerank,
        )
    except RuntimeError as e:
        # Config mancante o tutte le sotto-domande fallite
        log.exception("search_config_or_total_failure")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        log.exception("search_error")
        raise HTTPException(status_code=502, detail=f"Errore search: {e}")

    duration = round(time.monotonic() - t0, 3)
    log.info(
        "search_done",
        extra={
            "duration_s": duration,
            "n_risultati": len(result.get("risultati", [])),
            "n_candidati": result.get("n_candidati", 0),
        },
    )
    result["duration_s"] = duration
    return result


@app.post("/parse")
async def parse_file(
    request: Request,
    file: UploadFile = File(...),
    modulo: str = Form(...),
    categoria: str = Form(...),
    documento_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    # --- Rate limiting ---
    client_ip = request.client.host if request.client else "unknown"
    if _is_rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Troppe richieste. Riprova tra un minuto.")

    # --- Auth opzionale ---
    if PARSER_SHARED_SECRET:
        token = (authorization or "").replace("Bearer ", "")
        if token != PARSER_SHARED_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Validazione input form ---
    if not modulo or not modulo.strip():
        raise HTTPException(status_code=400, detail="modulo non può essere vuoto")
    if len(modulo) > _FIELD_MAX:
        raise HTTPException(status_code=400, detail=f"modulo: max {_FIELD_MAX} caratteri")
    if not categoria or not categoria.strip():
        raise HTTPException(status_code=400, detail="categoria non può essere vuota")
    if len(categoria) > _FIELD_MAX:
        raise HTTPException(status_code=400, detail=f"categoria: max {_FIELD_MAX} caratteri")
    if documento_id is not None and len(documento_id) > _FIELD_MAX:
        raise HTTPException(status_code=400, detail=f"documento_id: max {_FIELD_MAX} caratteri")

    # --- Sanitizzazione filename e validazione estensione ---
    safe_filename = sanitize_filename(file.filename)
    ext = PurePosixPath(safe_filename).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Estensione '{ext}' non supportata. Supportate: {sorted(SUPPORTED_EXTS)}",
        )

    # --- Validazione MIME (se fornito dal client) ---
    content_type = (file.content_type or "").lower().split(";")[0].strip()
    if content_type and content_type not in GENERIC_MIMES:
        expected = EXT_TO_MIME.get(ext, set())
        if content_type not in expected:
            raise HTTPException(
                status_code=400,
                detail=f"MIME '{content_type}' non corrisponde all'estensione '{ext}'. Atteso: {sorted(expected)}",
            )

    # --- Lettura con limite pre-RAM ---
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    cl = request.headers.get("content-length")
    if cl and int(cl) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File troppo grande (max {MAX_UPLOAD_MB}MB)")
    contents = await _read_limited(file, max_bytes)
    size_mb = len(contents) / (1024 * 1024)

    log.info("parse_start", extra={"file": safe_filename, "size_mb": round(size_mb, 3), "modulo": modulo})

    # --- 1. Estrai testo con Markitdown ---
    t0 = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: md_converter.convert_stream(io.BytesIO(contents), file_extension=ext),
            ),
            timeout=OCR_TIMEOUT,
        )
        markdown_text = (result.text_content or "").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=422, detail=f"Timeout OCR: documento troppo grande o complesso (>{OCR_TIMEOUT:.0f}s)")
    except Exception as e:
        log.exception("Markitdown error")
        raise HTTPException(status_code=422, detail=f"Errore parsing: {e}")
    log.info("ocr_done", extra={"duration_s": round(time.monotonic() - t0, 3)})

    if not markdown_text:
        raise HTTPException(
            status_code=422,
            detail="Nessun testo estratto. Il documento potrebbe essere vuoto o un'immagine senza OCR.",
        )

    # --- 2. Chunking ---
    t1 = time.monotonic()
    chunks = chunk_markdown(markdown_text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP)
    if not chunks:
        raise HTTPException(status_code=422, detail="Nessun chunk generato")
    log.info("chunking_done", extra={"n_chunks": len(chunks), "duration_s": round(time.monotonic() - t1, 3)})

    # --- 2b. Riassunto documento (contextual retrieval) ---
    riassunto = None
    if CONTEXTUAL_EMBEDDING:
        ts = time.monotonic()
        riassunto = await _safe_document_summary(markdown_text)
        log.info("summary_done", extra={"has_summary": riassunto is not None, "duration_s": round(time.monotonic() - ts, 3)})

    # --- 3. Embedding + classificazione multi-disciplina in parallelo ---
    # L'input dell'embedding è arricchito col contesto (titolo + riassunto +
    # breadcrumb di sezione); il testo salvato in `contenuto` resta pulito.
    # La classificazione lavora sul testo pulito e gira in parallelo: nel caso
    # tipico non aggiunge latenza rispetto al solo embedding.
    t2 = time.monotonic()
    if CONTEXTUAL_EMBEDDING:
        texts = [
            build_contextual_text(c["contenuto"], c["heading_path"], safe_filename, riassunto)
            for c in chunks
        ]
    else:
        texts = [c["contenuto"] for c in chunks]

    classify_task = asyncio.ensure_future(
        _safe_classify_chunks([c["contenuto"] for c in chunks], modulo)
    )
    try:
        embeddings = await generate_embeddings_batch(texts)
    except Exception as e:
        classify_task.cancel()
        log.exception("Embedding error")
        raise HTTPException(status_code=502, detail=f"Errore OpenAI: {e}")
    enrichments = await classify_task
    log.info("embedding_done", extra={"n_texts": len(texts), "duration_s": round(time.monotonic() - t2, 3)})

    # --- 4. Costruisci risposta pronta per INSERT su Supabase ---
    # Schema multidisciplinare: il modulo del form resta la prima disciplina
    # (retrocompatibilità); la classificazione LLM aggiunge le altre.
    modulo_norm = modulo.strip().lower()
    chunks_out = [
        {
            "chunk_index": c["chunk_index"],
            "contenuto": c["contenuto"],
            "embedding": emb,
            "heading": c["heading"],
            "heading_path": c["heading_path"],
            "modulo": modulo,
            "discipline": [modulo] + [d for d in enr["discipline"] if d != modulo_norm],
            "tags": enr["tags"],
            "entities": enr["entities"],
            "categoria": categoria,
            "documento_id": documento_id,
        }
        for c, emb, enr in zip(chunks, embeddings, enrichments)
    ]

    return {
        "markdown": markdown_text,
        "chunks": chunks_out,
        "metadata": {
            "file": safe_filename,
            "size_mb": round(size_mb, 2),
            "modulo": modulo,
            "categoria": categoria,
            "n_chunks": len(chunks),
            "riassunto_documento": riassunto,
            "embedding_model": "text-embedding-3-small",
            "embedding_dim": EMBEDDING_DIM,
        },
    }
