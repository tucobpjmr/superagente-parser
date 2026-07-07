# Handoff — SuperAgente Parser

> Stato al 2026-07-01 · branch `claude/handoff-alignment-review-ea2f32`
> PR #7 mergiata (commit `5316474`) · anello A risolto (`scripts/ingest.py`) · PR #4/#5 chiuse

## Changelog rispetto al handoff precedente (2026-06-10)

- ✅ **Anello A risolto**: aggiunto `scripts/ingest.py` — script standalone che
  chiama `/parse` e fa l'INSERT su Supabase (`documenti` + `document_chunks`) con
  la service_role key. Idempotente per `content_hash`, con `--force`, `--dry-run`,
  `--dir`, batching e rollback anti-orfani. Coperto da 13 test (`tests/test_ingest.py`).
  Suite totale: **103 test verdi**.
- ✅ **PR #4 e #5 chiuse** (item C): `.env.example` già in main, piano superseduto da #7.
- ✅ **Supabase riattivato** (2026-07-07): il progetto `pxtwdhhulobyrheioiex` è di
  nuovo `ACTIVE_HEALTHY`. Nota: nel dashboard si chiama "super-agente-viaggi", ma è
  il progetto RAG (ha `documenti`/`document_chunks`/`match_chunks`); il progetto
  "tullio" (`vmxvnxsqfisucugcpqlc`) è l'app task, senza schema RAG.
- ✅ **Smoke test DB reale eseguito** (2026-07-07, via MCP): ingestione simulata con
  le stesse scritture di `ingest.py` sul DB di produzione → INSERT documento+chunk
  (literal pgvector ok), colonna FTS generata, `match_chunks` con ranking corretto
  (denso+FTS+boost disciplina x1.5), lookup cache per `content_hash`, DELETE con
  cascade. DB ripulito a fine test (0 documenti / 0 chunk).
- ⚠️ **Vincolo ambiente Claude Code**: la network policy del container blocca ogni
  host esterno (railway.app, supabase.co REST, api.openai.com) — l'unico canale è
  l'MCP Supabase. **L'ingestione reale con `scripts/ingest.py` va eseguita dalla
  macchina dell'utente**, non da una sessione remota.

---

## 1. Panoramica del progetto

Microservizio Python/FastAPI deployato su **Railway** che espone 5 endpoint:

```
Browser / Next.js → [PARSER_SHARED_SECRET] → POST /parse   → OpenAI embeddings
                                            → POST /search  → Supabase pgvector (match_chunks RPC)
                                            → POST /answer  → Supabase + OpenAI (sintesi)
                                            → GET  /health  → liveness
                                            → GET  /ready   → readiness
```

**Repo**: `tucobpjmr/superagente-parser`
**Supabase progetto RAG**: `pxtwdhhulobyrheioiex` · `https://pxtwdhhulobyrheioiex.supabase.co`
**Supabase progetto storico** (super-agente-viaggi): `vmxvnxsqfisucugcpqlc` (non toccato)

---

## 2. Stato del codice (main, commit `5316474`)

### File principali

```
main.py          — FastAPI app + tutti gli endpoint
chunker.py       — Chunking markdown (heading gerarchico, heading_path)
embeddings.py    — Client OpenAI, batch parallelo, retry
enrichment.py    — Riassunto LLM + classificazione multi-disciplina + entità
search.py        — Fase 2: decompose → embed → fan-out match_chunks → RRF → rerank
answer.py        — Fase 3.1: search_pipeline + sintesi LLM con citazioni [n]
cache.py         — D4: lookup content_hash su Supabase prima di OCR+embedding
requirements.txt — 7 dipendenze (markitdown 0.1.6, fastapi, openai, httpx, tenacity)
Dockerfile       — Python 3.11-slim + Tesseract ITA/ENG + Poppler
railway.toml     — Railway build/deploy config
.env.example     — Tutte le env var documentate con commenti
scripts/
  ingest.py      — Ingestione standalone: /parse → INSERT Supabase (anello A)
supabase/        — Migrazioni SQL applicate (schema RAG, match_chunks RPC)
tests/           — 103 test (tutti passano)
  test_api.py        — 23 test endpoint /parse
  test_search.py     — 13 test /search pipeline
  test_answer.py     — 9 test /answer pipeline
  test_cache.py      — 7 test cache D4
  test_e2e_pdf.py    — 1 test E2E con PDF reale (markitdown non mockato)
  test_chunker.py    — 9 test chunker
  test_enrichment.py — test classificazione e riassunto
  test_filename.py   — 8 test sanitize_filename
  test_ingest.py     — 13 test ingest.py (parser+Supabase mockati)
eval/
  golden.jsonl   — 22 domande con discipline attese + substrings surrogati
  run_eval.py    — Misura recall@k, MRR, discipline coverage contro /search
  README.md      — Istruzioni annotazione chunk_ids
  fixtures/
    sample.pdf   — PDF minimale per test E2E
```

### Endpoint

| Metodo | Path | Uso |
|--------|------|-----|
| `GET` | `/health` | Liveness (Railway) — nessuna I/O |
| `GET` | `/ready` | Readiness — verifica OPENAI_API_KEY e markitdown |
| `POST` | `/parse` | Parsing + embedding + enrichment; JSON out pronto per INSERT Supabase |
| `POST` | `/search` | Retrieval: decompose → fan-out RRF → rerank |
| `POST` | `/answer` | Sintesi con citazioni: search + LLM |

### Tassonomia discipline (chiusa, in `enrichment.py`)

```
fiscalita, contrattualistica, assicurazioni, trasporti, turismo,
normativa-ue, privacy, contabilita, visti-documenti, dogane,
salute-sicurezza, controversie
```
Sovrascrivibile via env `DISCIPLINE_TAXONOMY` (CSV).

---

## 3. Variabili d'ambiente complete

### Già impostate su Railway (presupposte)

| Variabile | Note |
|-----------|------|
| `OPENAI_API_KEY` | Obbligatoria |
| `PARSER_SHARED_SECRET` | Auth condiviso tra tutti gli endpoint |

### ⚠️ DA IMPOSTARE SU RAILWAY — senza queste `/search` e `/answer` tornano 503

| Variabile | Valore | Note |
|-----------|--------|------|
| `SUPABASE_URL` | `https://pxtwdhhulobyrheioiex.supabase.co` | |
| `SUPABASE_SERVICE_KEY` | dashboard → Project Settings → API → **service_role** (secret) | mai la anon key |

### Opzionali con default

| Variabile | Default | Uso |
|-----------|---------|-----|
| `MAX_UPLOAD_MB` | `10` | |
| `CHUNK_SIZE` | `500` | |
| `CHUNK_OVERLAP` | `50` | |
| `OCR_TIMEOUT` | `60` | |
| `CONTEXTUAL_EMBEDDING` | `true` | Antepone titolo+riassunto+breadcrumb all'input embedding |
| `SUMMARY_TIMEOUT` | `15` | Timeout LLM riassunto documento |
| `CLASSIFY_TIMEOUT` | `45` | Timeout classificazione chunk |
| `ENRICHMENT_ENABLED` | `true` | Disattiva tutte le chiamate LLM di enrichment |
| `ENRICHMENT_MODEL` | `gpt-4o-mini` | Modello per riassunto + classificazione |
| `ENRICHMENT_BATCH_SIZE` | `16` | Chunk per chiamata classificazione |
| `DISCIPLINE_TAXONOMY` | (usa default) | CSV per sovrascrivere tassonomia |
| `PARSE_CACHE` | `true` | Cache SHA-256 su Supabase (richiede SUPABASE_*) |
| `SEARCH_TOP_K` | `8` | Risultati finali /search e /answer |
| `SEARCH_DECOMPOSE` | `true` | Decomposizione query in sotto-domande |
| `SEARCH_RERANK` | `llm` | `llm` oppure `none` |
| `DECOMPOSE_MODEL` | `gpt-4o-mini` | |
| `RERANK_MODEL` | `gpt-4o-mini` | |
| `SEARCH_HTTP_TIMEOUT` | `15` | Timeout Supabase RPC |
| `SEARCH_LLM_TIMEOUT` | `20` | Timeout LLM per decompose/rerank |
| `ANSWER_MODEL` | `gpt-4o` | Modello sintesi finale |
| `ANSWER_LLM_TIMEOUT` | `45` | |
| `ANSWER_CHUNK_CHARS` | `1500` | Troncamento chunk nel prompt sintesi |
| `ANSWER_MAX_CITATIONS` | `12` | Cap chunk passati al sintetizzatore |
| `PARSE_RATE_LIMIT` | `10` | Richieste per finestra |
| `PARSE_RATE_WINDOW` | `60` | Finestra rate limit (secondi) |
| `ALLOWED_ORIGINS` | (vuoto) | CORS, CSV (es. `https://app.example.com`) |
| `LOG_FORMAT` | `json` | `json` oppure `text` |

---

## 4. ⚠️ Anelli mancanti — da completare nella prossima sessione

### A. KB vuota — nessun documento ingerito (BLOCCO CRITICO)

La knowledge base Supabase ha **0 documenti e 0 chunk**.
`/search` e `/answer` funzionano tecnicamente ma restituiscono sempre risultati vuoti.

**Flusso di ingestion ATTUALE (incompleto):**

```
PDF → POST /parse → JSON con chunks+embeddings
                              ↓
               ❌ ANELLO MANCANTE: nessuno fa l'INSERT su Supabase
```

`/parse` **restituisce** i chunk pronti ma **non scrive** su Supabase — lo deve fare il caller.
Il caller previsto è la route Next.js `app/api/parse-file/route.ts`, che non è in questo repo.

**Soluzioni possibili (in ordine di urgenza):**

**1. Script standalone `scripts/ingest.py`** — ✅ **IMPLEMENTATO** (questa sessione).
Chiama `/parse` e fa l'INSERT su Supabase direttamente. Permette di popolare la KB
senza dipendere dal frontend Next.js. Idempotente per `content_hash`, con
`--force`/`--dry-run`/`--dir`, batching a 100 chunk e rollback anti-orfani.
Coperto da `tests/test_ingest.py`. Uso documentato nel README.
**Blocco residuo**: serve solo riattivare il progetto Supabase e impostare le env
(vedi sotto), poi lo script popola la KB.

Logica implementata (per riferimento):
```python
# 1. Leggi file locale
# 2. POST multipart a /parse: file, modulo, categoria
# 3. Estrai JSON: markdown, chunks[], metadata
# 4. INSERT su documenti:
#      nome_file, tipo_file, categoria, modulo
#      contenuto_testo = markdown
#      riassunto = metadata["riassunto_documento"]
#      content_hash = metadata["content_hash"]   ← FONDAMENTALE per cache
#      n_chunks = metadata["n_chunks"]
# 5. Per ogni chunk in chunks[]:
#      INSERT su document_chunks con documento_id appena creato
# 6. Stampa documento_id e n_chunk inseriti
```

Target CLI:
```bash
python scripts/ingest.py \
  --parser-url https://<railway>.up.railway.app \
  --secret $PARSER_SHARED_SECRET \
  --supabase-url https://pxtwdhhulobyrheioiex.supabase.co \
  --supabase-key $SUPABASE_SERVICE_KEY \
  --file documento.pdf \
  --modulo contrattualistica \
  --categoria normativa
```

**2. Route Next.js** (nell'altro repo) deve fare:
- Ricevere il file dal browser
- POST a `/parse` col secret
- INSERT su `documenti` (salvare `metadata.content_hash` in `documenti.content_hash`)
- INSERT su `document_chunks` per ogni elemento in `chunks[]`

**Schema INSERT `document_chunks`:**
```json
{
  "documento_id": "<uuid da documenti>",
  "chunk_index": 0,
  "contenuto": "...",
  "embedding": [0.012, ...],
  "heading": "Titolo > Sottotitolo",
  "modulo": "contrattualistica",
  "categoria": "normativa",
  "discipline": ["contrattualistica", "normativa-ue"],
  "tags": ["rimborso", "annullamento"],
  "entities": {"riferimenti_normativi": ["art. 41 CdT"]}
}
```

### B. Cache D4 non attivabile senza INSERT corretto lato caller

`/parse` restituisce `metadata.content_hash` (SHA-256 dei byte del file).
Il caller **deve** salvarlo in `documenti.content_hash` all'INSERT.
Senza questo la cache non ha mai hit (lookup per hash → nessun match).

### C. PR #4 e #5 ✅ CHIUSE (questa sessione)

- **PR #4** (`chore: add .env.example`) — chiusa: `.env.example` già in `main`.
- **PR #5** (`docs: piano di sviluppo`) — chiusa: superseduta da #7.

### D. RLS su 6 tabelle Supabase — debito D7 (sicurezza alta priorità)

Tabelle esposte senza RLS: `istruzioni_agente`, `fonti_online`, `documenti`,
`conversazioni`, `dati_climatici`, `requisiti_visti`.
Chiunque con la anon key può leggere/scrivere tutto.

**Non applicare senza prima definire le policy**: abilitare RLS senza policy blocca ogni accesso.

Da fare quando si sa come accede l'app Next.js:
- Service key lato server → policy che permettono tutto alla service key
- Anon key dal browser → policy con accesso pubblico in lettura (o con auth utente)

### E. Rate limiting distribuito — debito D1 (priorità media)

Implementazione attuale: in-memory per processo.
Con più replica Railway il limite non è condiviso.
Fix: Redis (Upstash, gratuito su Railway). Da fare solo se si scala a più worker.

---

## 5. Come eseguire i test

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/ -v
# Expected: 103 passed
# Non richiede OPENAI_API_KEY né Supabase: tutto mockato
```

---

## 6. Roadmap — stato aggiornato

| Fase | Descrizione | Stato |
|------|-------------|-------|
| 0 | Schema Supabase (pgvector, match_chunks RRF, HNSW, FTS) | ✅ completa — migrazioni applicate |
| 1.3 | Chunking contestuale (heading_path, contextual text) | ✅ completa |
| 1.1 | Classificazione multi-disciplina (tassonomia chiusa, batch LLM) | ✅ completa |
| 1.2 | Entità ponte (norme, importi, date, organismi → jsonb) | ✅ completa |
| 2 | `POST /search` (decompose + fan-out RRF + rerank) | ✅ completa |
| 3.1 | `POST /answer` (sintesi con citazioni, anti-allucinazione) | ✅ completa |
| 3.2 | Golden set di valutazione (22 domande, run_eval.py) | ✅ struttura — chunk_ids da annotare dopo ingestion |
| D4 | Caching embedding per content_hash | ✅ completa — richiede INSERT corretto lato caller |
| **A** | **Script ingestione standalone** (`scripts/ingest.py`) | ✅ **completa — 13 test** |
| **B** | **Route Next.js** con INSERT su Supabase | ❌ mancante — nell'altro repo |
| **C** | Chiudere PR #4 e #5 stantie | ✅ chiuse |
| **∅** | **Riattivare progetto Supabase** (`INACTIVE`) | 🔴 blocca ogni test live |
| **D7** | RLS Supabase sulle 6 tabelle | 🔴 alta priorità sicurezza |
| **D1** | Rate limiting distribuito (Redis/Upstash) | 🟡 solo se si scala |
| D5 | pytest-cov > 80% su main.py | 🟡 bassa |
| D6 | Dependency pinning con pip-compile | 🟡 bassa |

---

## 7. Ordine di esecuzione consigliato — prossima sessione

> Restano **solo passi manuali/live** (env, riattivazione DB, smoke test): il
> codice per l'ingestione è pronto e testato.

### Step 0 — Riattivare il progetto Supabase (manuale) 🔴 NUOVO

Il progetto `pxtwdhhulobyrheioiex` è **`INACTIVE`**. Dal dashboard Supabase →
Restore/Resume project. Senza questo, ingestione e `/search`/`/answer` non hanno DB.

### Step 1 — Impostare env Railway (manuale)

Nel progetto Railway del parser aggiungere:
- `SUPABASE_URL` = `https://pxtwdhhulobyrheioiex.supabase.co`
- `SUPABASE_SERVICE_KEY` = dashboard Supabase → Project Settings → API → service_role

Railway dovrebbe rideploy automaticamente da `main` dopo il merge di #7.
Verificare che `/health` e `/ready` rispondano 200.

### Step 2 — Popolare la KB con `scripts/ingest.py` ✅ (script pronto)

Lo script è implementato e testato. Basta eseguirlo su documenti reali (vedi
README, sezione "Popolare la knowledge base"). Sblocca test live, eval e cache D4.

### Step 3 — Smoke test live

Dopo aver ingerito almeno 2-3 documenti reali:

```bash
# Ingestione
python scripts/ingest.py \
  --parser-url $PARSER_URL --secret $SECRET \
  --supabase-url $SUPABASE_URL --supabase-key $SUPABASE_SERVICE_KEY \
  --file documento.pdf --modulo contrattualistica --categoria normativa

# /search
curl -X POST $PARSER_URL/search \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'

# /answer (stessa domanda — verificare citazioni [n] inline e array citazioni[])
curl -X POST $PARSER_URL/answer \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'
```

Verificare:
- `/search`: `sotto_domande` ha decomposizione sensata, `risultati` pertinenti, `nome_file` presente
- `/answer`: risposta con `[n]` inline, array `citazioni[]` allineato agli indici
- **Anti-allucinazione**: domanda fuori KB → *"Non ho trovato fonti pertinenti…"* (non invenzione)

### Step 4 — Baseline eval

```bash
python eval/run_eval.py \
  --base-url $PARSER_URL \
  --secret $SECRET \
  --top-k 8 \
  --json-out eval/baseline_$(date +%Y%m%d).jsonl
```

Metriche: recall@k, MRR, discipline coverage (separati mono/multi-disciplina).
Poi annotare i `chunk_ids` reali in `eval/golden.jsonl` per recall@k affidabile.

### Step 5 — RLS Supabase

Chiedere al proprietario: l'app Next.js accede a Supabase con service_role key
lato server o con anon key dal browser? Poi scrivere e applicare le migrazioni RLS.

### Step 6 — Chiudere PR #4 e #5

Basta chiuderle su GitHub (nessuna azione di codice).

---

## 8. Architettura di riferimento

```
Browser
  │
  ▼
Next.js API route (app/api/parse-file/route.ts)   ← repo separato — ANELLO MANCANTE B
  │  PARSER_SHARED_SECRET
  ├─► POST /parse  ──► Markitdown OCR → chunker → OpenAI embeddings → enrichment LLM
  │                          │
  │   INSERT su Supabase ◄───┘   ← ANELLO MANCANTE A (o via scripts/ingest.py)
  │     documenti.content_hash    (abilita cache D4)
  │     document_chunks[]
  │
  ├─► POST /search ──► decompose LLM → embed → match_chunks RPC (pgvector+FTS+RRF) → rerank LLM
  └─► POST /answer ──► /search + gpt-4o sintesi con citazioni [n]

Supabase (pxtwdhhulobyrheioiex):
  documenti          — metadati file + content_hash + riassunto + n_chunks
  document_chunks    — testo + embedding(1536) + discipline[] + tags[] + entities + fts
  match_chunks RPC   — dense cosine + FTS italian + RRF + boost x1.5 per disciplina
```
