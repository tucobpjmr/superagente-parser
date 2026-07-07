# Handoff — SuperAgente Parser

> Stato al 2026-07-07 · branch `claude/handoff-alignment-review-ea2f32` (PR #8 draft)
> Ultimo commit: `main` = `245c47e`, branch = 3 commit avanti

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
  ⚠️ Nel dashboard si chiama "super-agente-viaggi" — ma è il DB RAG corretto.
**Supabase progetto app task** (Tullio): `vmxvnxsqfisucugcpqlc` — non toccarlo.

---

## 2. Stato del codice (branch `claude/handoff-alignment-review-ea2f32`)

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
  ingest.py      — Ingestione standalone: chiama /parse → INSERT su Supabase
                   (anello A, implementato 2026-07-01)
supabase/migrations/
  20260609000000_rag_schema.sql          — Schema RAG (documenti, document_chunks, indici)
  20260609000001_match_chunks.sql        — RPC match_chunks (RRF ibrida densa+FTS)
  20260609000002_relax_legacy_checks.sql — Rimozione CHECK legacy troppo rigidi
  20260707000000_security_fixes.sql      — search_path fisso, drop indice duplicato
  20260707000001_rls_policies.sql        — RLS + policy SELECT public su tutte le tabelle
  20260707000002_drop_legacy_match_chunks.sql — Drop versione legacy match_chunks
tests/           — 103 test (tutti passano)
  test_api.py        — 23 test endpoint /parse
  test_search.py     — 13 test /search pipeline
  test_answer.py     — 9 test /answer pipeline
  test_cache.py      — 7 test cache D4
  test_e2e_pdf.py    — 1 test E2E con PDF reale (markitdown non mockato)
  test_chunker.py    — 9 test chunker
  test_enrichment.py — test classificazione e riassunto
  test_filename.py   — 8 test sanitize_filename
  test_ingest.py     — 13 test ingest.py (parser+Supabase mockati httpx)
eval/
  golden.jsonl   — 22 domande con discipline attese + substrings surrogati
  run_eval.py    — Misura recall@k, MRR, discipline coverage contro /search
  README.md      — Istruzioni annotazione chunk_ids
  fixtures/
    sample.pdf   — PDF minimale per test E2E
```

---

## 3. Variabili d'ambiente

### Già impostate su Railway (presupposte)

| Variabile | Note |
|-----------|------|
| `OPENAI_API_KEY` | Obbligatoria |
| `PARSER_SHARED_SECRET` | Auth condiviso tra tutti gli endpoint |

### ⚠️ DA IMPOSTARE SU RAILWAY — senza queste `/search` e `/answer` tornano 503

| Variabile | Valore |
|-----------|--------|
| `SUPABASE_URL` | `https://pxtwdhhulobyrheioiex.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Dashboard Supabase → Project Settings → API → **service_role** |

### Opzionali con default

Documentate in `.env.example` (completo). Default ragionevoli per produzione.

---

## 4. Stato Supabase (pxtwdhhulobyrheioiex) — al 2026-07-07

| Cosa | Stato |
|------|-------|
| Progetto | `ACTIVE_HEALTHY` |
| Schema RAG (`documenti`, `document_chunks`, `match_chunks`) | ✅ applicato |
| RLS su tutte le 6 tabelle pubbliche | ✅ abilitata con policy SELECT public |
| `match_chunks` — search_path fisso | ✅ |
| `aggiorna_timestamp` — search_path fisso | ✅ |
| `match_chunks` versione legacy rimossa | ✅ |
| Indice duplicato `idx_document_chunks_documento` | ✅ rimosso |
| Security advisor Supabase | **0 ERROR, 2 WARN** (pg_trgm e vector in public — won't-fix, standard Supabase) |
| Documenti nella KB | **0** — KB vuota, da popolare con `scripts/ingest.py` |

---

## 5. PR aperta

**PR #8** `feat: scripts/ingest.py — ingestione standalone KB Supabase (anello A)`
- URL: https://github.com/tucobpjmr/superagente-parser/pull/8
- Stato: **draft, mergeable, 0 CI check** (nessun workflow nel repo)
- Diff: +802 -13 righe su 4 file (`scripts/ingest.py`, `tests/test_ingest.py`, `README.md`, `HANDOFF.md`)
- Da fare: mergiare in `main` quando pronto (non ha conflitti)

---

## 6. Roadmap — stato completo

| Item | Descrizione | Stato |
|------|-------------|-------|
| Fase 0 | Schema Supabase (pgvector, match_chunks RRF, HNSW, FTS) | ✅ |
| Fase 1.1–1.3 | Chunking contestuale, classificazione multi-disciplina, entità | ✅ |
| Fase 2 | `POST /search` (decompose + fan-out RRF + rerank) | ✅ |
| Fase 3.1 | `POST /answer` (sintesi con citazioni, anti-allucinazione) | ✅ |
| Fase 3.2 | Golden set valutazione (22 domande) — struttura pronta | ✅ struttura |
| D4 | Cache embedding per content_hash | ✅ |
| **A** | Script ingestione standalone (`scripts/ingest.py`) | ✅ **PR #8** |
| **D7** | RLS Supabase su 6 tabelle + fix sicurezza funzioni | ✅ **applicato** |
| C | PR #4 e #5 stantie | ✅ chiuse |
| **B** | Route Next.js con INSERT su Supabase | ❌ nell'altro repo |
| **ENV** | `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` su Railway | ⚠️ **da impostare (manuale)** |
| **KB** | Ingestione documenti reali | ⚠️ **0 doc — blocca smoke test e eval** |
| Fase 3.2 | Annotare chunk_ids reali in `golden.jsonl` | 🟡 dopo ingestione |
| D1 | Rate limiting distribuito (Redis/Upstash) | 🟡 solo se si scala |
| D5 | pytest-cov > 80% su main.py | 🟡 bassa |
| D6 | Dependency pinning con pip-compile | 🟡 bassa |

---

## 7. Prossima sessione — roadmap per Claude Cowork

> **Prerequisiti manuali** (tuo intervento, ~5 min):
> 1. Mergare PR #8 in `main` su GitHub
> 2. Railway → aggiungere `SUPABASE_URL` e `SUPABASE_SERVICE_KEY`
> 3. Ingestione di almeno 3-5 documenti reali dalla tua macchina (istruzioni sotto)

### Step 1 — Ingestione documenti reali (dalla tua macchina)

```bash
# Clona il branch aggiornato (o usa main dopo il merge di PR #8)
git clone -b claude/handoff-alignment-review-ea2f32 \
  https://github.com/tucobpjmr/superagente-parser
cd superagente-parser
pip install httpx   # unica dipendenza extra (httpx è già in requirements.txt)

# Ingestione singolo file
python scripts/ingest.py \
  --parser-url https://<railway>.up.railway.app \
  --secret $PARSER_SHARED_SECRET \
  --supabase-url https://pxtwdhhulobyrheioiex.supabase.co \
  --supabase-key $SUPABASE_SERVICE_KEY \
  --modulo contrattualistica --categoria normativa \
  --file documento.pdf

# Ingestione intera cartella
python scripts/ingest.py ... --dir ./documenti-kb --modulo turismo --categoria faq

# Dry-run (verifica senza scrivere)
python scripts/ingest.py ... --file doc.pdf --dry-run
```

### Step 2 — Smoke test live (per Claude Cowork, richiede Railway URL + secret)

```bash
# Liveness
curl $PARSER_URL/health

# Readiness (verifica OpenAI key e Supabase)
curl $PARSER_URL/ready

# Search
curl -X POST $PARSER_URL/search \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'

# Answer (stessa domanda — verificare citazioni [n] e array citazioni[])
curl -X POST $PARSER_URL/answer \
  -H "Authorization: Bearer $SECRET" \
  -H "Content-Type: application/json" \
  -d '{"domanda":"Il cliente annulla la crociera per malattia: ha diritto al rimborso?"}'
```

Verificare:
- `/search`: `sotto_domande` con decomposizione sensata, `risultati` pertinenti
- `/answer`: risposta con `[n]` inline, `citazioni[]` allineato agli indici
- **Anti-allucinazione**: domanda fuori KB → *"Non ho trovato fonti pertinenti…"*

### Step 3 — Baseline eval

```bash
python eval/run_eval.py \
  --base-url $PARSER_URL \
  --secret $SECRET \
  --top-k 8 \
  --json-out eval/baseline_$(date +%Y%m%d).jsonl
```

Poi annotare i `chunk_ids` reali in `eval/golden.jsonl` per recall@k affidabile.

### Step 4 — Route Next.js (altro repo)

Deve fare:
1. Ricevere file dal browser
2. POST a `/parse` con secret
3. INSERT su `documenti` (salvare `metadata.content_hash` → abilita cache D4)
4. INSERT su `document_chunks` per ogni elemento in `chunks[]`

Schema INSERT `document_chunks`:
```json
{
  "documento_id": "<uuid da documenti>",
  "chunk_index": 0,
  "contenuto": "...",
  "embedding": "[0.012,...]",
  "heading": "Titolo > Sottotitolo",
  "modulo": "contrattualistica",
  "categoria": "normativa",
  "discipline": ["contrattualistica", "normativa-ue"],
  "tags": ["rimborso", "annullamento"],
  "entities": {"riferimenti_normativi": ["art. 41 CdT"]}
}
```

### Step 5 — Restrizione RLS `conversazioni` (quando si implementa auth)

Attualmente policy permissiva in lettura. Quando l'app avrà auth utente:
```sql
-- Sostituire la policy "conversazioni_select_public" con:
drop policy "conversazioni_select_public" on conversazioni;
create policy "conversazioni_select_own"
  on conversazioni for select
  using (auth.uid()::text = user_id);  -- adattare al nome colonna reale
```

---

## 8. Come eseguire i test

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/ -v
# Expected: 103 passed
# Non richiede OPENAI_API_KEY né Supabase: tutto mockato
```

---

## 9. Architettura di riferimento

```
Browser
  │
  ▼
Next.js API route (app/api/parse-file/route.ts)   ← repo separato — anello B
  │  PARSER_SHARED_SECRET
  ├─► POST /parse  ──► Markitdown OCR → chunker → OpenAI embeddings → enrichment LLM
  │                          │
  │   INSERT su Supabase ◄───┘   ← content_hash obbligatorio (abilita cache D4)
  │     documenti
  │     document_chunks[]
  │                     (oppure: python scripts/ingest.py — anello A ✅)
  │
  ├─► POST /search ──► decompose LLM → embed → match_chunks RPC (pgvector+FTS+RRF) → rerank LLM
  └─► POST /answer ──► /search + gpt-4o sintesi con citazioni [n]

Supabase (pxtwdhhulobyrheioiex — "super-agente-viaggi" nel dashboard):
  documenti          — metadati file + content_hash + riassunto + n_chunks
  document_chunks    — testo + embedding(1536) + discipline[] + tags[] + entities + fts
  match_chunks RPC   — dense cosine + FTS italian + RRF + boost x1.5 per disciplina
  RLS                — tutte le tabelle protette, SELECT public, scrittura solo service_role
```
