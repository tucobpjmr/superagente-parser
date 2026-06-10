# Handoff — SuperAgente Parser

> Stato al 2026-06-10 · branch `main` · PR #7 mergiata (squash, commit `5316474`)

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
supabase/        — Migrazioni SQL applicate (schema RAG, match_chunks RPC)
tests/           — 90 test (tutti passano su main)
  test_api.py        — 23 test endpoint /parse
  test_search.py     — 13 test /search pipeline
  test_answer.py     — 9 test /answer pipeline
  test_cache.py      — 7 test cache D4
  test_e2e_pdf.py    — 1 test E2E con PDF reale (markitdown non mockato)
  test_chunker.py    — 9 test chunker
  test_enrichment.py — test classificazione e riassunto
  test_filename.py   — 8 test sanitize_filename
eval/
  golden.jsonl   — 22 domande con discipline attese + substrings surrogati
  run_eval.py    — Misura recall@k, MRR, discipline coverage contro /search
  fixtures/
    sample.pdf   — PDF minimale per test E2E
```

### Tassonomia discipline (chiusa, da `enrichment.py`)

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

### DA IMPOSTARE SU RAILWAY — senza queste /search e /answer tornano 503

| Variabile | Valore | Note |
|-----------|--------|------|
| `SUPABASE_URL` | `https://pxtwdhhulobyrheioiex.supabase.co` | |
| `SUPABASE_SERVICE_KEY` | vedi dashboard → Project Settings → API → service_role (secret) | mai la anon key |

### Opzionali con default

| Variabile | Default | Uso |
|-----------|---------|-----|
| `MAX_UPLOAD_MB` | `10` | |
| `CHUNK_SIZE` | `500` | |
| `CHUNK_OVERLAP` | `50` | |
| `OCR_TIMEOUT` | `60` | |
| `CONTEXTUAL_EMBEDDING` | `true` | Antepone titolo+riassunto+breadcrumb all'input embedding |
| `SUMMARY_TIMEOUT` | `15` | Timeout LLM per riassunto documento |
| `CLASSIFY_TIMEOUT` | `45` | Timeout classificazione chunk |
| `ENRICHMENT_ENABLED` | `true` | Disattiva tutte le chiamate LLM di enrichment |
| `ENRICHMENT_MODEL` | `gpt-4o-mini` | Modello per riassunto + classificazione |
| `ENRICHMENT_BATCH_SIZE` | `16` | Chunk per chiamata classificazione |
| `DISCIPLINE_TAXONOMY` | (usa default) | CSV per sovrascrivere tassonomia |
| `PARSE_CACHE` | `true` | Cache SHA-256 su Supabase (richiede SUPABASE_*) |
| `SEARCH_TOP_K` | `8` | Risultati finali /search e /answer |
| `SEARCH_DECOMPOSE` | `true` | Decomposizione query in sotto-domande |
| `SEARCH_RERANK` | `llm` | llm oppure none |
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
| `ALLOWED_ORIGINS` | (vuoto) | CORS, CSV (es. https://app.example.com) |
| `LOG_FORMAT` | `json` | json oppure text |

---

## 4. Anelli mancanti — da completare nella prossima sessione

### A. KB vuota — nessun documento ingerito (BLOCCO CRITICO)

La knowledge base Supabase ha 0 documenti e 0 chunk.
/search e /answer funzionano tecnicamente ma restituiscono sempre vuoti.

Flusso di ingestion ATTUALE (incompleto):

```
PDF → POST /parse → JSON con chunks+embeddings
                              ↓
                   ANELLO MANCANTE: nessuno fa l'INSERT su Supabase
```

/parse restituisce i chunk pronti ma NON scrive su Supabase.
Il chiamante previsto è la route Next.js app/api/parse-file/route.ts, che non è in questo repo.

Soluzioni (in ordine di urgenza per i test):

1. Script standalone scripts/ingest.py — chiama /parse e fa l'INSERT su Supabase.
   Permette di popolare la KB senza dipendere dal frontend. DA IMPLEMENTARE.

2. Completare la route Next.js — nell'altro repo Next.js la route deve:
   - Ricevere il file dal browser
   - Postarlo a POST /parse col secret
   - INSERT su documenti (salvando metadata.content_hash in documenti.content_hash)
   - INSERT su document_chunks per ogni elemento in chunks[]

Schema INSERT per document_chunks:
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

### B. Cache non pienamente attivabile senza INSERT corretto lato caller

/parse restituisce metadata.content_hash (SHA-256 dei byte del file).
Il caller DEVE salvarlo in documenti.content_hash all'INSERT.
Senza di questo la cache non ha mai hit (nessun match per hash).

### C. PR #4 e #5 ancora aperte (stantie)

- PR #4 (chore: add .env.example) — l'.env.example è già in main da #7; chiuderla
- PR #5 (docs: piano di sviluppo) — documento superseduto; chiuderla

### D. RLS su 6 tabelle Supabase (debito D7 — sicurezza alta priorità)

Tabelle esposte senza RLS: istruzioni_agente, fonti_online, documenti,
conversazioni, dati_climatici, requisiti_visti.
Chiunque con la anon key può leggere/scrivere tutto.

NON applicare senza prima definire le policy: abilitare RLS senza policy blocca ogni accesso.

Da fare quando sai come accede l'app Next.js (service key lato server vs anon key dal browser).

### E. Rate limiting distribuito (debito D1 — priorità media)

L'implementazione attuale è in-memory per processo.
Con più replica Railway il limite non è condiviso.
Fix: Redis (Upstash, gratuito su Railway). Da fare solo se si scala a più worker.

---

## 5. Come eseguire i test

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/ -v
# Expected: 90 passed
# Non richiede OPENAI_API_KEY né Supabase: tutto mockato
```

---

## 6. Roadmap completa — stato aggiornato

| Fase | Descrizione | Stato |
|------|-------------|-------|
| 0 | Schema Supabase (pgvector, match_chunks RRF, HNSW, FTS) | COMPLETA — migrazioni applicate |
| 1.3 | Chunking contestuale (heading_path, contextual text) | COMPLETA |
| 1.1 | Classificazione multi-disciplina (tassonomia chiusa, batch LLM) | COMPLETA |
| 1.2 | Entità ponte (norme, importi, date, organismi → jsonb) | COMPLETA |
| 2 | POST /search (decompose + fan-out RRF + rerank) | COMPLETA |
| 3.1 | POST /answer (sintesi con citazioni, anti-allucinazione) | COMPLETA |
| 3.2 | Golden set di valutazione (22 domande, run_eval.py) | STRUTTURA CREATA — chunk_ids da annotare dopo ingestion |
| D4 | Caching embedding per content_hash | COMPLETA — richiede INSERT corretto lato caller |
| A | Script di ingestione standalone (scripts/ingest.py) | MANCANTE — blocca ogni test live |
| B | Route Next.js con INSERT su Supabase | MANCANTE — nell'altro repo |
| C | Chiudere PR #4 e #5 stantie | MINORE |
| D7 | RLS Supabase sulle 6 tabelle | ALTA PRIORITÀ SICUREZZA — richiede decisione policy |
| D1 | Rate limiting distribuito (Redis/Upstash) | SOLO SE si scala a più worker |
| D5 | pytest-cov > 80% su main.py | BASSA |
| D6 | Dependency pinning con pip-compile | BASSA |

---

## 7. Ordine di esecuzione consigliato per la prossima sessione

### Step 1 — Impostare env Railway (manuale, non automatizzabile)

Nel progetto Railway del parser aggiungere:
- SUPABASE_URL = https://pxtwdhhulobyrheioiex.supabase.co
- SUPABASE_SERVICE_KEY = dalla dashboard Supabase → Project Settings → API → service_role

Railway dovrebbe rideploy automaticamente da main dopo il merge di #7.
Verificare che /health e /ready rispondano 200.

### Step 2 — Implementare scripts/ingest.py (prima cosa da fare in sessione)

File da creare: scripts/ingest.py

Logica:
1. Leggere un file locale
2. POST multipart a /parse con file, modulo, categoria
3. Estrarre JSON: markdown, chunks[], metadata
4. INSERT su documenti con:
   - nome_file, tipo_file, categoria, modulo
   - contenuto_testo = markdown
   - riassunto = metadata.riassunto_documento
   - content_hash = metadata.content_hash   ← FONDAMENTALE per cache
   - n_chunks = metadata.n_chunks
5. Per ogni chunk in chunks[]: INSERT su document_chunks con documento_id appena creato
6. Stampare il documento_id creato e il numero di chunk inseriti

Client Supabase Python: usare httpx direttamente (già dipendenza) o aggiungere supabase-py.
Autenticazione: header apikey + Authorization Bearer con la SERVICE_KEY.

Esempio di invocazione target:
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

### Step 3 — Smoke test live

Dopo ingestion di almeno 2-3 documenti reali:

```bash
# /search
curl -X POST https://<railway>.up.railway.app/search \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'

# /answer
curl -X POST https://<railway>.up.railway.app/answer \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'
```

Verificare:
- /search: sotto_domande ha decomposizione sensata, risultati pertinenti, nome_file presente
- /answer: risposta con citazioni [n] inline, array citazioni[] allineato
- Anti-allucinazione: domanda fuori KB → dichiarazione esplicita di insufficienza

### Step 4 — Baseline eval

```bash
python eval/run_eval.py \
  --base-url https://<railway>.up.railway.app \
  --secret $PARSER_SHARED_SECRET \
  --top-k 8 \
  --json-out eval/baseline_$(date +%Y%m%d).jsonl
```

Metriche restituite: recall@k, MRR, discipline coverage (mono e multi-disciplina separati).
Poi annotare i chunk_ids reali in eval/golden.jsonl per avere recall@k affidabile.

### Step 5 — RLS Supabase (richede decisione proprietario)

Chiedere: l'app Next.js accede a Supabase con la service_role key lato server o con la anon key dal browser?

- Service key lato server → si può abilitare RLS con policy che permettono tutto alla service key
- Anon key dal browser → le policy devono permettere accesso pubblico in lettura (o con auth utente)

Poi nella prossima sessione scrivere e applicare le migrazioni RLS.

### Step 6 — Chiudere PR #4 e #5 stantie

Basta chiuderle su GitHub (nessuna azione di codice necessaria).

---

## 8. Architettura di riferimento

```
Browser
  │
  ▼
Next.js API route (app/api/parse-file/route.ts)   ← repo separato, anello mancante B
  │  PARSER_SHARED_SECRET
  ├─► POST /parse  ──► Markitdown OCR → chunker → OpenAI embeddings → enrichment LLM
  │                          │
  │   INSERT su Supabase ◄───┘   ← anello mancante A (o via scripts/ingest.py)
  │     documenti.content_hash    (abilita cache D4)
  │     document_chunks[]
  │
  ├─► POST /search ──► decompose (LLM) → embed → match_chunks RPC (pgvector+FTS+RRF) → rerank
  └─► POST /answer ──► /search + gpt-4o sintesi con citazioni [n]

Supabase (pxtwdhhulobyrheioiex):
  documenti          — metadati file + content_hash + riassunto + n_chunks
  document_chunks    — testo + embedding(1536) + discipline[] + tags[] + entities + fts
  match_chunks RPC   — dense cosine + FTS italian + RRF + boost x1.5 per disciplina
```
