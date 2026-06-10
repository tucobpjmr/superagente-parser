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

### `POST /search`
Ricerca ibrida RAG con decomposizione + fan-out + RRF + re-ranking.

**JSON body**:
```json
{
  "query": "Quali documenti servono per la Thailandia e che clima c'è a luglio?",
  "top_k": 8,
  "n_subqueries": 3,
  "per_sub_k": 12,
  "rerank": true
}
```

**Pipeline**:
1. **Decomposizione**: LLM scompone la domanda in sotto-domande, ognuna con disciplina target (`visti`, `meteo`, `controversie`, `generale`, …)
2. **Fan-out parallelo**: per ogni sotto-domanda, embedding + RPC `match_chunks` su Supabase (ibrida dense+FTS con RRF interno e boost ×1.5 sui chunk con disciplina matching)
3. **Fusione RRF** dei risultati tra sotto-domande
4. **Re-ranking** LLM opzionale dei top-20 candidati

**Risposta**:
```json
{
  "query": "...",
  "subqueries": [{"text": "...", "discipline": ["visti"]}, ...],
  "n_candidates": 14,
  "results": [{"id": "...", "documento_id": "...", "contenuto": "...", "heading": "...", "discipline": [...], "score": 0.83, "rrf_score": 0.042}],
  "duration_s": 1.42,
  "errors": null
}
```

Richiede env `SUPABASE_URL` e `SUPABASE_SERVICE_KEY` (vedi sotto). Auth: stesso `PARSER_SHARED_SECRET` di `/parse`.

### `GET /health`
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
   - `SUPABASE_URL` (es. `https://xxx.supabase.co`) — necessaria per `/search`
   - `SUPABASE_SERVICE_KEY` (service-role key, **non** la anon key) — necessaria per `/search`
   - `SEARCH_LLM_MODEL=gpt-4o-mini` (opzionale, default per decomposizione e re-ranking)
4. `railway up`
5. Copia l'URL pubblico → mettilo in `PYTHON_PARSER_URL` lato Next.js

## Test

```bash
pytest tests/
```

## OCR

OCR italiano + inglese inclusi nel Dockerfile (`tesseract-ocr-ita`, `tesseract-ocr-eng`).
Markitdown attiva l'OCR automaticamente quando il PDF non ha layer testuale.
