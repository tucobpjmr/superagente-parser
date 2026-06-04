# Guida progetto — superagente-parser

Microservizio FastAPI Python per parsing documenti + generazione embedding OpenAI.
Pensato per essere chiamato da un frontend Next.js / backend Supabase per preparare dati RAG.

## Architettura

```
HTTP POST /parse (multipart)
        ↓
   [main.py]          ← validazione, auth, orchestrazione
        ↓
   [Markitdown]       ← conversione → Markdown (OCR PDF/img inclusa)
        ↓
   [chunker.py]       ← split per heading, fallback word-count, merge piccoli
        ↓
   [embeddings.py]    ← OpenAI text-embedding-3-small, batch paralleli
        ↓
   JSON con chunks + embedding → pgvector
```

## File chiave

| File | Responsabilità |
|------|----------------|
| `main.py` | FastAPI app, endpoint `/parse` e `/health`, validazione, orchestrazione |
| `chunker.py` | Logica chunking markdown (heading-aware + word fallback) |
| `embeddings.py` | Client OpenAI async, batch paralleli, retry con backoff |
| `requirements.txt` | Dipendenze pin-versionate |
| `Dockerfile` | Build con tesseract-ocr per Railway |
| `railway.toml` | Config deploy Railway |
| `tests/test_chunker.py` | Unit test (pytest) |

## Variabili d'ambiente

| Variabile | Default | Note |
|-----------|---------|------|
| `OPENAI_API_KEY` | — | **Obbligatoria** |
| `PARSER_SHARED_SECRET` | — | Opzionale, abilita auth Bearer |
| `MAX_UPLOAD_MB` | `10` | Limite size upload |
| `ALLOWED_ORIGINS` | vuoto | CORS, comma-separated |
| `PORT` | `8000` | Porta Uvicorn |

## Comandi locali

```bash
# Setup
pip install -r requirements.txt
pip install pytest                      # solo dev

# Test
python -m pytest tests/ -v

# Run dev
export OPENAI_API_KEY=sk-...
uvicorn main:app --reload --port 8000

# Test endpoint
curl -X POST http://localhost:8000/parse \
  -F "file=@test.pdf" \
  -F "modulo=fiscalita" \
  -F "categoria=normativa"
```

## Convenzioni

- **Lingua**: log e messaggi di errore in italiano (target utenza italiana)
- **Async**: tutto async (FastAPI + AsyncOpenAI). Operazioni bloccanti (Markitdown) vanno in `run_in_executor` con `asyncio.wait_for`.
- **Errori**: `HTTPException` con status code semantici (400 input, 401 auth, 413 size, 422 parsing, 502 OpenAI).
- **Test**: solo pytest, no framework BDD. I test importano i moduli direttamente.

## Deployment

Railway con Dockerfile. Healthcheck su `GET /health`. 2 worker Uvicorn.
