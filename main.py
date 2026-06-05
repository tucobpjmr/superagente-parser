"""
Microservizio FastAPI per parsing documenti + embedding.
Endpoint:
  POST /parse  → riceve file, ritorna chunk con embedding
  GET  /ready  → healthcheck per Railway (alias: /health)
"""

import io
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from markitdown import MarkItDown

from chunker import chunk_markdown
from embeddings import generate_embeddings_batch, EMBEDDING_DIM
from rate_limit import check_rate_limit


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
@app.get("/ready")
async def health():
    return {"status": "ok", "embedding_dim": EMBEDDING_DIM}


@app.post("/parse", dependencies=[Depends(check_rate_limit)])
async def parse_file(
    file: UploadFile = File(...),
    modulo: str = Form(...),
    categoria: str = Form(...),
    documento_id: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
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

    log.info(f"Parsing {file.filename} ({size_mb:.2f}MB, modulo={modulo})")

    # --- 1. Estrai testo con Markitdown ---
    try:
        # Markitdown gestisce OCR su immagini e PDF scansionati automaticamente
        # se le librerie OCR sono installate (vedi Dockerfile).
        result = md_converter.convert_stream(
            io.BytesIO(contents),
            file_extension=ext,
        )
        markdown_text = (result.text_content or "").strip()
    except Exception as e:
        log.exception("Markitdown error")
        raise HTTPException(status_code=422, detail=f"Errore parsing: {e}")

    if not markdown_text:
        raise HTTPException(
            status_code=422,
            detail="Nessun testo estratto. Il documento potrebbe essere vuoto o un'immagine senza OCR.",
        )

    # --- 2. Chunking ---
    chunks = chunk_markdown(markdown_text, chunk_size=500, overlap=50)
    if not chunks:
        raise HTTPException(status_code=422, detail="Nessun chunk generato")

    log.info(f"Generati {len(chunks)} chunk")

    # --- 3. Embedding batch ---
    try:
        texts = [c["contenuto"] for c in chunks]
        embeddings = await generate_embeddings_batch(texts)
    except Exception as e:
        log.exception("Embedding error")
        raise HTTPException(status_code=502, detail=f"Errore OpenAI: {e}")

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
