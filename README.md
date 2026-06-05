# SuperAgente Parser — Microservizio Python

Microservizio FastAPI per estrarre testo da documenti (PDF, DOCX, XLSX, PPTX, HTML, immagini con OCR), chunkare il contenuto e generare embedding OpenAI pronti per l'inserimento in pgvector.

## Endpoint

### `POST /parse`
**Form-data**:
- `file` (file): documento da processare
- `modulo` (str): es. `fiscalita`, `controversie`
- `categoria` (str): categoria libera
- `documento_id` (str, opz.): UUID del documento parent in `documenti`

**Risposta**:
```json
{
  "markdown": "...",
  "chunks": [
    {
      "chunk_index": 0,
      "contenuto": "...",
      "embedding": [0.012, -0.034, ...],
      "heading": "Capitolo 1",
      "modulo": "fiscalita",
      "categoria": "normativa",
      "documento_id": "uuid-..."
    }
  ],
  "metadata": { "file": "...", "n_chunks": 12, "embedding_dim": 1536, ... }
}
```

### `GET /ready` (alias: `/health`)
Healthcheck per Railway.

## Sviluppo locale

```bash
cp .env.example .env
# inserisci OPENAI_API_KEY

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Test:
```bash
curl -X POST http://localhost:8000/parse \
  -F "file=@sample.pdf" \
  -F "modulo=fiscalita" \
  -F "categoria=normativa"
```

## Deploy su Railway

1. `railway login`
2. `railway init` (nuovo progetto)
3. Imposta variabili:
   - `OPENAI_API_KEY`
   - `PARSER_SHARED_SECRET` (genera con `openssl rand -hex 32`)
   - `MAX_UPLOAD_MB=10`
   - (opz.) `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` per rate limiting distribuito
   - (opz.) `RATE_LIMIT_PER_MIN=10`
4. `railway up`
5. Copia l'URL pubblico → mettilo in `PYTHON_PARSER_URL` lato Next.js

## Rate limiting

Con più repliche, il rate limiter in-process non è coerente. Questo servizio usa
**Upstash Redis** via REST API (chiave per IP, finestra fissa 60s). Funziona da
Railway senza VPC peering.

- Imposta `UPSTASH_REDIS_REST_URL` + `UPSTASH_REDIS_REST_TOKEN` per attivarlo
- Senza queste variabili è disattivato (no-op) — ok per dev locale
- Fail-open in caso di errori Redis: un blip di rete non blocca gli upload
- Risposta 429 con header `Retry-After`

## Cache embedding

Le embedding sono deterministiche per `(modello, testo)`. Il servizio usa lo
**stesso Upstash** come cache LRU con TTL (default 30 giorni):

- Chiave: `emb:text-embedding-3-small:{sha256(testo)}`
- Pre-batch: bulk `GET` sui chunk → solo i miss vanno a OpenAI
- Post-batch: bulk `SET` dei nuovi embedding con TTL
- Disattivato se Upstash non configurato; fail-open su errori Redis
- Risparmio reale su documenti con boilerplate ripetuto (intestazioni, clausole standard)

## Test

```bash
pytest tests/
```

## OCR

OCR italiano + inglese inclusi nel Dockerfile (`tesseract-ocr-ita`, `tesseract-ocr-eng`).
Markitdown attiva l'OCR automaticamente quando il PDF non ha layer testuale.
