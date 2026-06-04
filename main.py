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
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
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

# Limite upload (MB) — proteggi container e timeout Vercel
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))

# Auth opzionale per il microservizio (consigliata in produzione)
PARSER_SHARED_SECRET = os.getenv("PARSER_SHARED_SECRET")

app = FastAPI(title="SuperAgente Parser", version="2.0")

# CORS: solo se il microservizio viene chiamato direttamente dal browser.
# In architettura standard, è Next.js che lo chiama → CORS non serve.
# Lasciato restrittivo per default.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "").split(",") if os.getenv("ALLOWED_ORIGINS") else [],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

md_converter = MarkItDown()


@app.get("/health")
async def health():
    return {"status": "ok", "embedding_dim": EMBEDDING_DIM}


@app.post("/parse")
async def parse_file(
    file: UploadFile = File(...),
    modulo: str = Form(...),
    categoria: str = Form(...),
    documento_id: Optional[str] = Form(None),
    authorization: Optional[str] = None,
):
    # --- Auth opzionale ---
    if PARSER_SHARED_SECRET:
        token = (authorization or "").replace("Bearer ", "")
        if token != PARSER_SHARED_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    # --- Validazione estensione ---
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Estensione '{ext}' non supportata. Supportate: {sorted(SUPPORTED_EXTS)}",
        )

    # --- Lettura con limite dimensione ---
    contents = await file.read()
    size_mb = len(contents) / (1024 * 1024)
    if size_mb > MAX_UPLOAD_MB:
        raise HTTPException(
            status_code=413,
            detail=f"File troppo grande: {size_mb:.1f}MB (max {MAX_UPLOAD_MB}MB)",
        )

    t0 = time.perf_counter()
    log.info(f"Parsing {file.filename} ({size_mb:.2f}MB, modulo={modulo})")

    # --- 1. Estrai testo con Markitdown ---
    # Eseguito in thread executor con timeout: l'OCR su PDF grandi può bloccarsi a lungo.
    try:
        loop = asyncio.get_running_loop()
        buf = io.BytesIO(contents)
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: md_converter.convert_stream(buf, file_extension=ext),
            ),
            timeout=60.0,
        )
        markdown_text = (result.text_content or "").strip()
    except asyncio.TimeoutError:
        raise HTTPException(status_code=422, detail="Timeout parsing documento (>60s)")
    except Exception as e:
        log.exception("Markitdown error")
        raise HTTPException(status_code=422, detail=f"Errore parsing: {e}")

    t_md = time.perf_counter()
    log.info(f"Markitdown: {t_md - t0:.2f}s, chars={len(markdown_text)}")

    if not markdown_text:
        raise HTTPException(
            status_code=422,
            detail="Nessun testo estratto. Il documento potrebbe essere vuoto o un'immagine senza OCR.",
        )

    # --- 2. Chunking ---
    chunks = chunk_markdown(markdown_text, chunk_size=500, overlap=50)
    if not chunks:
        raise HTTPException(status_code=422, detail="Nessun chunk generato")

    t_chunk = time.perf_counter()
    log.info(f"Chunking: {t_chunk - t_md:.2f}s, chunks={len(chunks)}")

    # --- 3. Embedding batch (parallelo) ---
    try:
        texts = [c["contenuto"] for c in chunks]
        embeddings = await generate_embeddings_batch(texts)
    except Exception as e:
        log.exception("Embedding error")
        raise HTTPException(status_code=502, detail=f"Errore OpenAI: {e}")

    t_emb = time.perf_counter()
    log.info(f"Embedding: {t_emb - t_chunk:.2f}s | Totale: {t_emb - t0:.2f}s")

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
