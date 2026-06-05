# SuperAgente Parser — Handoff & Roadmap

> Documento di contesto per sessioni successive in Claude Code (web/cowork).
> Aggiornato: 2026-06-05 — branch attivo: `claude/confident-fermat-bXn8f` → PR #4

---

## 1. Cos'è questo servizio

Microservizio **Python / FastAPI** deployato su **Railway** che:

1. Riceve un documento (PDF, DOCX, XLSX, PPTX, HTML, immagini) via `POST /parse`
2. Estrae testo con **Markitdown** (OCR incluso via Tesseract)
3. Chunka il markdown per heading + word-count con overlap
4. Genera embedding **OpenAI text-embedding-3-small** (1536 dim)
5. Restituisce `{ markdown, chunks[{contenuto, embedding, heading, ...}], metadata }`

Il client principale è un'app **Next.js** (`tucobpjmr/super-agente-viaggi`) che:
- Carica il file via `app/api/parse-file/route.ts` (file di riferimento in `nextjs-reference/`)
- Inserisce i chunk in **Supabase** tabella `document_chunks` con pgvector

---

## 2. Stato attuale del codebase

### File principali

| File | Ruolo |
|---|---|
| `main.py` | FastAPI app, endpoint `/parse` + `/ready` (`/health`) |
| `chunker.py` | Split per heading → fallback word-count con overlap |
| `embeddings.py` | Batch OpenAI con cache SHA-256 lookup pre-batch |
| `embedding_cache.py` | Upstash Redis REST: `get_many` / `set_many` con TTL |
| `rate_limit.py` | Rate limiter distribuito per IP via Upstash (fixed-window) |

### Infrastruttura esterna usata

| Servizio | Variabile env | Usato per |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | Embedding text-embedding-3-small |
| Upstash Redis | `UPSTASH_REDIS_REST_URL` + `_TOKEN` | Cache embedding + rate limiting |
| Railway | — | Deploy container (Dockerfile) |

### Variabili d'ambiente (vedi `.env.example`)

```
OPENAI_API_KEY=
PARSER_SHARED_SECRET=          # auth Bearer opzionale
MAX_UPLOAD_MB=10
ALLOWED_ORIGINS=               # CORS
UPSTASH_REDIS_REST_URL=        # attiva cache + rate limit
UPSTASH_REDIS_REST_TOKEN=
RATE_LIMIT_PER_MIN=10
RATE_LIMIT_TIMEOUT_S=1.0
EMBEDDING_CACHE_TTL_S=2592000  # 30 giorni
EMBEDDING_CACHE_TIMEOUT_S=2.0
```

### Test suite

```bash
pytest tests/ --cov=main --cov=rate_limit --cov=embedding_cache --cov=embeddings
```

- **40 test**, 100% pass
- Coverage: `main.py` 98%, `rate_limit.py` 92%, `embedding_cache.py` 92%, `embeddings.py` 79%
- `fail_under = 80` configurato in `pyproject.toml`
- `tests/conftest.py` stubbba `markitdown` per ambienti CI senza dipendenze native

### Deploy

```bash
railway up          # usa requirements.lock con --require-hashes
# healthcheck: GET /ready (alias /health)
# workers: 2 uvicorn
```

Lock file generato con:
```bash
pip-compile --generate-hashes --output-file=requirements.lock --strip-extras --no-header requirements.txt
```

---

## 3. PR aperte / stato branch

| PR | Branch | Stato | Contenuto |
|---|---|---|---|
| #4 | `claude/confident-fermat-bXn8f` | **Draft — pronta per review** | Tutto ciò che è sotto questa sezione |

**Commits in PR #4** (sopra `main`):
1. `.env.example` aggiunto
2. Healthcheck `/ready` (alias `/health`)
3. Auth header fix critico (`Header(None)` invece di `None`)
4. pytest-cov, test suite completa, 98% coverage main.py
5. `requirements.lock` con hash verificati
6. markitdown 0.1.1 → 0.1.6
7. Rate limiter distribuito Upstash
8. Cache embedding SHA-256 Upstash

**PR chiuse:**
- #3 mergiata su `main` (fix auth + OCR non-bloccante + embedding parallelo)
- #2 chiusa (incorporata in #3)
- #1 chiusa con istruzioni per `super-agente-viaggi` (file in `nextjs-reference/`)

---

## 4. Problemi noti / tech debt

| Priorità | Problema | Note |
|---|---|---|
| Media | `embeddings.py` coverage 79% | `_embed_batch` con retry tenacity non testato (richiede mock OpenAI più profondo) |
| Media | `python-dotenv` in `requirements.txt` mai importato | Rimasto da dipendenza storica; può essere rimosso |
| Bassa | `asyncio_mode` genera warning pytest | Risolto con `pytest-asyncio` ma warning persiste su config option |
| Bassa | `nextjs-reference/` nel repo parser | File di riferimento senza test; potrebbe spostarsi su `super-agente-viaggi` |

---

## 5. Roadmap

### Immediato (prossima sessione)

- [ ] **Mergiare PR #4** su main dopo review
- [ ] **Rimuovere `python-dotenv`** da `requirements.txt` (mai usato) + rigenera lock
- [ ] Aumentare coverage `embeddings.py` 79% → >85% (mock tenacity retry)

### Breve termine

- [ ] **GitHub Actions CI**: `pytest --cov` + `pip install --require-hashes -r requirements.lock` su push/PR
- [ ] **Upstash cache stats endpoint**: `GET /cache/stats` → `{ enabled, ttl, model }` per monitoring
- [ ] **Chunk deduplication**: se due chunk hanno stesso SHA-256, invia embedding una volta sola (oggi vengono mandati entrambi a OpenAI)
- [ ] **`/parse` response time header**: `X-Parse-Ms`, `X-Embedding-Ms`, `X-Cache-Hits` per diagnostica in produzione

### Medio termine

- [ ] **Supabase insert diretto** (opzionale): con `SUPABASE_SERVICE_ROLE_KEY` il parser potrebbe inserire i chunk direttamente invece di restituirli al client Next.js — riduce payload di rete per documenti >500 chunk
- [ ] **Presidio della dimensione token**: attualmente `chunk_size=500 parole`; per documenti multilanguage conviene passare a token-count (tiktoken) per stare sotto il limite di context-window downstream
- [ ] **Cache invalidation**: endpoint `DELETE /cache/{sha256}` per forzare re-embedding di un chunk specifico

### Lungo termine / architetturale

- [ ] **Streaming SSE**: `POST /parse/stream` con `text/event-stream` per documenti >300 chunk — restituisce progress events man mano che i batch vengono embeddati (attivo solo su deploy non-Vercel o Vercel Pro con timeout >60s)
- [ ] **Worker separato**: se il volume cresce, separare OCR+chunking (sync, CPU-bound) dall'embedding (async, I/O-bound) in due container Railway distinti con una coda Redis Streams

---

## 6. Come riprendere il lavoro in una nuova sessione

```bash
# Branch attivo
git checkout claude/confident-fermat-bXn8f
git pull origin claude/confident-fermat-bXn8f

# Installa dipendenze (prima volta)
pip install -r requirements.txt

# Verifica tutto verde
pytest tests/ --cov=main --cov=rate_limit --cov=embedding_cache --cov=embeddings -q

# Rigenera lock dopo modifiche a requirements.txt
pip-compile --generate-hashes --output-file=requirements.lock --strip-extras --no-header requirements.txt
```

### Repository correlati

| Repo | Accesso | Note |
|---|---|---|
| `tucobpjmr/superagente-parser` | Scope attuale | Questo repo |
| `tucobpjmr/super-agente-viaggi` | Da aggiungere allo scope | Frontend Next.js + Supabase |
| `tucobpjmr/TULLIO` | — | Gestionale task, non correlato |

Per aggiungere `super-agente-viaggi` allo scope della sessione Claude Code: *Settings → Repositories → Add*.
