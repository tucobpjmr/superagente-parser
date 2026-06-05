# Handoff — SuperAgente Parser

> Stato al 2026-06-05 · branch `claude/priority-1-resolution-VMFeX` · PR #3 (draft)

---

## 1. Panoramica del progetto

Microservizio Python/FastAPI deployato su **Railway** che:
1. Riceve un documento (PDF, DOCX, XLSX, PPTX, HTML, immagini) via `POST /parse`
2. Estrae testo con **Markitdown** (OCR automatico via Tesseract)
3. Spezza il testo in **chunk** per heading + word-count
4. Genera **embedding** con OpenAI `text-embedding-3-small`
5. Restituisce chunk + embedding pronti per INSERT in **Supabase pgvector**

Il chiamante principale è la route Next.js `app/api/parse-file/route.ts` (già scaffoldata in PR #1).

```
Browser → Next.js API route → [PARSER_SHARED_SECRET] → POST /parse → OpenAI Embeddings
                                                                     ↓
                                                              Supabase document_chunks
```

---

## 2. Cosa è stato fatto in questa sessione

### PR #3 — `claude/priority-1-resolution-VMFeX` (branch corrente)

5 commit in ordine cronologico:

| Commit | Tag | Contenuto |
|--------|-----|-----------|
| `72ad06a` | fix | **Auth header critico**: `authorization: Optional[str] = None` → `Header(None)`. FastAPI non iniettava mai l'HTTP header → con `PARSER_SHARED_SECRET` impostato ogni richiesta era sempre 401. |
| `72ad06a` | perf | **OCR non-bloccante**: `run_in_executor` + `wait_for(OCR_TIMEOUT)`. **Embedding parallelo**: `asyncio.gather` sui batch (da ~3s a ~1s con 300 chunk). |
| `9a8952e` | security | **Pre-RAM size check**: `_read_limited()` legge in chunk da 64 KB, rifiuta prima di caricare tutto in RAM. **Validazione form**: modulo/categoria non-vuoti, max 200 char. **Rate limiting**: sliding window per IP → 429. **Bug chunker**: coda orfana, O(n²) concatenation, mutazione input. |
| `54cc11d` | robustness | **sanitize_filename**: strip path POSIX/Windows, Unicode NFKC, char di controllo, troncamento con estensione preservata. **MIME check**: verifica coerenza content-type / estensione. **CORS fix**: parsing robusto ALLOWED_ORIGINS, OPTIONS, allow_headers ristretto. **`/ready`** endpoint per readiness probe. |
| `ac88c44` | quality | **Log strutturato JSON** (`_JsonFormatter`, `LOG_FORMAT` env). **Config hardcoded → env vars** (`CHUNK_SIZE`, `CHUNK_OVERLAP`, `OCR_TIMEOUT`, `PARSE_RATE_WINDOW`, `EMBEDDING_MODEL`, `EMBEDDING_BATCH_SIZE`). **threading.Lock rimosso** (asyncio è single-threaded per processo). **40 test** (test_api.py, test_chunker.py, test_filename.py). |

### PR aperte in parallelo

| PR | Branch | Stato | Contenuto |
|----|--------|-------|-----------|
| **#1** | `claude/exciting-tesla-vqJIP` | draft | Route Next.js `app/api/parse-file/route.ts` per Fase 3 RAG |
| **#2** | `claude/travel-agent-architecture-gFUUz` | draft | Ottimizzazioni PERF già incorporate nel branch corrente |
| **#3** | `claude/priority-1-resolution-VMFeX` | **draft** | Tutti i fix P1/P2/P3 — da mergere su `main` |

> **Azione richiesta**: fare review e merge di PR #3 → poi valutare se PR #1 è ancora necessaria (il codice Next.js va nel repo Next.js, non qui).

---

## 3. Stato attuale del codice

### File principali

```
main.py          — FastAPI app, tutti gli endpoint, helpers
chunker.py       — Chunking markdown (heading + word-count + merge)
embeddings.py    — Client OpenAI, batch parallelo, retry tenacity
requirements.txt — 7 dipendenze (rimosso python-dotenv inutile)
Dockerfile       — Python 3.11-slim + Tesseract ITA/ENG + Poppler
railway.toml     — Railway build/deploy config
tests/
  test_api.py       — 23 test di integrazione endpoint
  test_chunker.py   — 9 test per chunker
  test_filename.py  — 8 test per sanitize_filename
```

### Variabili d'ambiente

| Variabile | Default | Obbligatoria |
|-----------|---------|:---:|
| `OPENAI_API_KEY` | — | ✓ |
| `PARSER_SHARED_SECRET` | — | consigliata |
| `MAX_UPLOAD_MB` | `10` | |
| `PARSE_RATE_LIMIT` | `10` | |
| `PARSE_RATE_WINDOW` | `60` | |
| `CHUNK_SIZE` | `500` | |
| `CHUNK_OVERLAP` | `50` | |
| `OCR_TIMEOUT` | `60` | |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | |
| `EMBEDDING_BATCH_SIZE` | `100` | |
| `ALLOWED_ORIGINS` | `` (CORS disabilitato) | |
| `LOG_FORMAT` | `json` | |

### Endpoint

| Metodo | Path | Uso |
|--------|------|-----|
| `GET` | `/health` | Liveness probe (Railway HEALTHCHECK) — <100 ms, no I/O |
| `GET` | `/ready` | Readiness probe — verifica OPENAI_API_KEY e markitdown |
| `POST` | `/parse` | Parsing + embedding — form-data: `file`, `modulo`, `categoria`, `documento_id?` |

---

## 4. Come eseguire i test

```bash
pip install -r requirements.txt pytest
pytest tests/ -v
# Expected: 40 passed
```

Il file `.env.example` è **mancante** (TODO nella roadmap).  
Per i test non serve OPENAI_API_KEY: le chiamate OpenAI sono mockate.

---

## 5. Roadmap

### Fase immediatamente successiva

- [ ] **Merge PR #3** su `main` dopo review
- [ ] **Creare `.env.example`** con tutte le variabili documentate (il README la referenzia ma il file non esiste)
- [ ] **Chiudere PR #2** (le ottimizzazioni PERF sono già in PR #3; PR #2 è obsoleta)
- [ ] **PR #1** — Spostare `nextjs-reference/` nel repo Next.js; chiudere questa PR o convertirla in nota

### Qualità e osservabilità

- [ ] **pytest coverage**: aggiungere `pytest-cov`; target > 80% su `main.py`
- [ ] **`.env.example`**: documentare tutte le env var con commenti
- [ ] **`railway.toml`**: aggiornare `healthcheckPath` da `/health` a `/ready` per readiness corretta
- [ ] **Secrets scanning**: aggiungere `gitleaks` o GitHub secret scanning al CI
- [ ] **Aggiornamento dipendenze**: `markitdown[all]==0.1.1` è datata (verificare versione corrente)

### Scalabilità e produzione

- [ ] **Rate limiting distribuito**: l'implementazione attuale è per-processo (in-memory). Con più worker/replica su Railway, il rate limit non è condiviso. Fix: Redis (upstash è gratuito su Railway) o sticky-session per IP.
- [ ] **Streaming response**: per documenti grandi (>1000 chunk), la risposta JSON può essere > 10 MB. Valutare `StreamingResponse` o paginazione.
- [ ] **Job asincrono / worker**: per documenti molto grandi, spostare il parsing su una coda (Railway workers, BullMQ, o Supabase Edge Functions con queue). L'endpoint `/parse` restituirebbe un `job_id` e il cliente farebbe polling.
- [ ] **Caching embedding**: documenti identici producono gli stessi embedding. Un hash SHA-256 del contenuto + lookup in Supabase eviterebbe chiamate OpenAI duplicate.

### Testing

- [ ] **Test E2E**: aggiungere un test che usa un vero file PDF piccolo (fixture in `tests/fixtures/`) per validare l'intera pipeline OCR → chunk → embedding mock.
- [ ] **Load test**: verificare il comportamento del rate limiter con `locust` o `k6`.
- [ ] **Mutational testing**: valutare `mutmut` per verificare la qualità dei test del chunker.

### Sicurezza (post-hardening)

- [ ] **Aggiungere `X-Request-ID`** header per correlazione log tra Next.js e parser
- [ ] **Audit log**: loggare modulo/categoria/documento_id di ogni parse con l'IP (già nel log strutturato, ma verificare che sia inviato a un sistema di audit)
- [ ] **Verifica contenuto file**: Markitdown accetta qualsiasi file con estensione supportata. Valutare deep content inspection (es. verifica magic bytes PDF) per prevenire file crafted malevoli.
- [ ] **Dependency pinning**: usare `pip-compile` (`pip-tools`) per generare un `requirements.lock` deterministico con hash verificati.
