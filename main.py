"""
Microservizio FastAPI per parsing documenti + embedding.
Endpoint:
  POST /parse  → riceve file, ritorna chunk con embedding
  GET  /health → healthcheck per Railway
"""

import asyncio
import io
import logging
import os
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from markitdown import MarkItDown

from chunker import chunk_markdown
from embeddings import generate_embeddings_batch, EMBEDDING_DIM


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parser")

# Estensioni supportate da Markitdown
SUPPORTED_EXTS = {
    ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".ppt",
    ".html", ".htm", ".txt", ".md", ".csv", ".json",
    ".png", ".jpg", ".jpeg", ".webp",  # OCR via Markitdown
}

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
PARSER_SHARED_SECRET = os.getenv("PARSER_SHARED_SECRET")

# Rate limiting: max N richieste/minuto per IP (protezione DoS e costi OpenAI)
_RATE_LIMIT = int(os.getenv("PARSE_RATE_LIMIT", "10"))
_RATE_WINDOW = 60  # secondi
_rate_store: dict = defaultdict(list)
_rate_lock = threading.Lock()

_FIELD_MAX = 200  # lunghezza massima campi form

app = FastAPI(title="SuperAgente Parser", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else [],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

md_converter = MarkItDown()


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - _RATE_WINDOW
    with _rate_lock:
        _rate_store[ip] = [t for t in _rate_store[ip] if t > cutoff]
        if len(_rate_store[ip]) >= _RATE_LIMIT:
            return True
        _rate_store[ip].append(now)
        return False


async def _read_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Legge il file in chunk da 64KB rifiutando se supera max_bytes prima di caricare tutto in RAM."""
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


@app.get("/health")
async def health():
    return {"status": "ok", "embedding_dim": EMBEDDING_DIM}


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

    # --- Validazione estensione ---
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Estensione '{ext}' non supportata. Supportate: {sorted(SUPPORTED_EXTS)}",
        )

    # --- Lettura con limite pre-RAM ---
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    # Fast-reject tramite Content-Length se presente (il valore include headers multipart,
    # quindi è una stima superiore: sicuro usarlo solo per rigetto rapido)
    cl = request.headers.get("content-length")
    if cl and int(cl) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File troppo grande (max {MAX_UPLOAD_MB}MB)")
    contents = await _read_limited(file, max_bytes)
    size_mb = len(contents) / (1024 * 1024)

    log.info(f"Parsing {file.filename} ({size_mb:.2f}MB, modulo={modulo})")

    # --- 1. Estrai testo con Markitdown ---
    t0 = time.monotonic()
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: md_converter.convert_stream(io.BytesIO(contents), file_extension=ext),
            ),
            timeout=60.0,
        )
        markdown_text = (result.text_content or "").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=422, detail="Timeout OCR: documento troppo grande o complesso (>60s)")
    except Exception as e:
        log.exception("Markitdown error")
        raise HTTPException(status_code=422, detail=f"Errore parsing: {e}")
    log.info(f"OCR completato in {time.monotonic() - t0:.2f}s")

    if not markdown_text:
        raise HTTPException(
            status_code=422,
            detail="Nessun testo estratto. Il documento potrebbe essere vuoto o un'immagine senza OCR.",
        )

    # --- 2. Chunking ---
    t1 = time.monotonic()
    chunks = chunk_markdown(markdown_text, chunk_size=500, overlap=50)
    if not chunks:
        raise HTTPException(status_code=422, detail="Nessun chunk generato")
    log.info(f"Chunking: {len(chunks)} chunk in {time.monotonic() - t1:.2f}s")

    # --- 3. Embedding batch ---
    t2 = time.monotonic()
    try:
        texts = [c["contenuto"] for c in chunks]
        embeddings = await generate_embeddings_batch(texts)
    except Exception as e:
        log.exception("Embedding error")
        raise HTTPException(status_code=502, detail=f"Errore OpenAI: {e}")
    log.info(f"Embedding: {len(texts)} testi in {time.monotonic() - t2:.2f}s")

    # --- 4. Costruisci risposta pronta per INSERT su Supabase ---
    chunks_out = [
        {
            "chunk_index": c["chunk_index"],
            "contenuto": c["contenuto"],
            "embedding": emb,
            "heading": c["heading"],
            "modulo": modulo,
            "categoria": categoria,
            "documento_id": documento_id,
        }
        for c, emb in zip(chunks, embeddings)
    ]

    return {
        "markdown": markdown_text,
        "chunks": chunks_out,
        "metadata": {
            "file": file.filename,
            "size_mb": round(size_mb, 2),
            "modulo": modulo,
            "categoria": categoria,
            "n_chunks": len(chunks),
            "embedding_model": "text-embedding-3-small",
            "embedding_dim": EMBEDDING_DIM,
        },
    }
