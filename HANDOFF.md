# Handoff — SuperAgente Parser

> Stato al 2026-06-09 · branch `claude/super-agent-strategy-ylp0fd` · PR #6 (draft)

---

## 1. Panoramica del progetto

Microservizio Python/FastAPI deployato su **Railway** che costituisce il
backend di ingestion e retrieval del super agente. Il flusso principale:

```
Browser → Next.js API route → [PARSER_SHARED_SECRET] → POST /parse
                                                              │
                          Markitdown (OCR) → chunking → arricchimento LLM
                                                              │
                                          discipline[] + tags + entities
                                                              │
                                              embedding OpenAI (contestuale)
                                                              │
                                          Supabase: documenti, document_chunks
                                                              │
                                     (Fase 2, non ancora implementato)
                                          POST /search → match_chunks RRF
```

### Progetto Supabase attivo

| Progetto | ID | Stato |
|----------|----|-------|
| `super-agente-viaggi` | `pxtwdhhulobyrheioiex` | ACTIVE_HEALTHY |
| `tullio` | `vmxvnxsqfisucugcpqlc` | ACTIVE_HEALTHY (gestionale) |

Lo schema RAG vive su **`super-agente-viaggi`** (ripristinato da pausa il 2026-06-09).

---

## 2. Struttura del repo

```
main.py              — FastAPI app, endpoint /parse /health /ready, helpers
chunker.py           — Chunking markdown (heading path gerarchico + word-count)
embeddings.py        — Client OpenAI, batch parallelo, retry tenacity
enrichment.py        — Riassunto documento + classificazione multi-disciplina + entità
requirements.txt     — 7 dipendenze (markitdown 0.1.6)
Dockerfile           — Python 3.11-slim + Tesseract ITA/ENG + Poppler
railway.toml         — healthcheckPath = "/ready"
supabase/migrations/
  20260609000000_rag_schema.sql      — tabelle documenti/document_chunks, indici HNSW+GIN
  20260609000001_match_chunks.sql    — funzione match_chunks (RRF ibrido)
  20260609000002_relax_legacy_checks.sql — rimozione CHECK troppo rigidi
tests/
  test_api.py        — 33 test di integrazione endpoint
  test_chunker.py    — 16 test (chunker + contextual text)
  test_enrichment.py — 11 test (classificazione + entità)
  test_filename.py   — 8 test
PIANO-SVILUPPO-MULTIDISCIPLINARE.md — piano originale (su branch PR #5, solo doc)
```

**Suite corrente: 60 passed** (zero fallimenti).

---

## 3. Fasi completate

### Fase 0 — Fondamenta Supabase ✅

Migrazioni **applicate e verificate** su `super-agente-viaggi`:

| Oggetto | Dettaglio |
|---------|-----------|
| `documenti` | UUID, nome_file, discipline text[], riassunto, content_hash, n_chunks |
| `document_chunks` | UUID, embedding vector(1536), fts tsvector (italian), discipline text[], tags text[], entities jsonb, heading text, heading_path text |
| Indice HNSW | `document_chunks_embedding_idx` (cosine) |
| Indice GIN | `document_chunks_fts_idx`, `document_chunks_discipline_idx`, `documenti_discipline_idx` |
| `match_chunks()` | Ricerca ibrida: densa + full-text (BM25), fusione RRF, boost ×1.5 per disciplina (mai filtro rigido) |

I CHECK legacy `documenti_categoria_check` e `documenti_tipo_file_check`
(troppo restrittivi) sono stati rimossi.

### Fase 1.3 — Chunking contestuale ✅

- `chunk_markdown()` traccia la **gerarchia heading completa** (heading_path:
  `H1 > H2 > H3`) con stack di antenati.
- `build_contextual_text()`: antepone
  `[Doc: <titolo> — <riassunto> — Sezione: <heading_path>]` all'**input
  dell'embedding**. Il testo salvato in `contenuto` resta pulito.
- `generate_document_summary()`: 1 chiamata LLM per documento (gpt-4o-mini),
  gated da `ENRICHMENT_ENABLED`.
- `_safe_document_summary()` in `main.py`: timeout 15 s + cattura totale.

### Fase 1.1/1.2 — Classificazione multi-disciplina + entità ✅

- `classify_chunks()`: batch paralleli, ogni batch = 1 chiamata LLM.
- Risposta JSON validata in `_parse_classification()`: filtro tassonomia,
  dedup, cap 8 tag, `entities` normalizzate.
- Tassonomia built-in di 12 discipline (csv env `DISCIPLINE_TAXONOMY`).
- Classificazione eseguita **in parallelo all'embedding** via `asyncio.ensure_future`.
- Degradazione a 3 livelli: JSON malformato → neutro; batch fallito → neutro
  per il batch; timeout/errore globale → `discipline=[modulo]`.
- `/parse` output per chunk: `discipline` (modulo sempre primo), `tags`,
  `entities`, `heading_path`.

---

## 4. Endpoint attuali

| Metodo | Path | Uso |
|--------|------|-----|
| `GET` | `/health` | Liveness probe (<100 ms) |
| `GET` | `/ready` | Readiness: verifica OPENAI_API_KEY + markitdown |
| `POST` | `/parse` | Parsing + embedding + arricchimento LLM |

### Risposta `/parse` (schema attuale)

```json
{
  "markdown": "...",
  "chunks": [
    {
      "chunk_index": 0,
      "contenuto": "testo pulito...",
      "embedding": [0.012, ...],
      "heading": "Sezione",
      "heading_path": "Capitolo > Sezione",
      "modulo": "viaggi",
      "discipline": ["viaggi", "fiscalita", "contrattualistica"],
      "tags": ["penale annullamento", "iva"],
      "entities": {
        "riferimenti_normativi": ["Dlgs 62/2024"],
        "importi": ["250 EUR"]
      },
      "categoria": "normativa",
      "documento_id": "uuid-..."
    }
  ],
  "metadata": {
    "file": "contratto.pdf",
    "size_mb": 0.45,
    "modulo": "viaggi",
    "categoria": "normativa",
    "n_chunks": 12,
    "riassunto_documento": "Condizioni generali di vendita pacchetti turistici.",
    "embedding_model": "text-embedding-3-small",
    "embedding_dim": 1536
  }
}
```

---

## 5. Variabili d'ambiente

| Variabile | Default | Obbligatoria | Fase |
|-----------|---------|:---:|------|
| `OPENAI_API_KEY` | — | ✓ | — |
| `PARSER_SHARED_SECRET` | — | consigliata | — |
| `MAX_UPLOAD_MB` | `10` | | — |
| `PARSE_RATE_LIMIT` | `10` | | — |
| `PARSE_RATE_WINDOW` | `60` | | — |
| `CHUNK_SIZE` | `500` | | — |
| `CHUNK_OVERLAP` | `50` | | — |
| `OCR_TIMEOUT` | `60` | | — |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | | — |
| `EMBEDDING_BATCH_SIZE` | `100` | | — |
| `ALLOWED_ORIGINS` | `` | | — |
| `LOG_FORMAT` | `json` | | — |
| `CONTEXTUAL_EMBEDDING` | `true` | | 1.3 |
| `SUMMARY_TIMEOUT` | `15` | | 1.3 |
| `ENRICHMENT_ENABLED` | `true` | | 1 |
| `ENRICHMENT_MODEL` | `gpt-4o-mini` | | 1 |
| `SUMMARY_MAX_INPUT_CHARS` | `12000` | | 1.3 |
| `DISCIPLINE_TAXONOMY` | built-in | | 1.1 |
| `ENRICHMENT_BATCH_SIZE` | `16` | | 1.1 |
| `ENRICHMENT_CHUNK_CHARS` | `2000` | | 1.1 |
| `CLASSIFY_TIMEOUT` | `45` | | 1.1 |
| `SUPABASE_URL` | — | ✓ (Fase 2) | 2 |
| `SUPABASE_SERVICE_KEY` | — | ✓ (Fase 2) | 2 |

---

## 6. PR aperte

| PR | Branch | Stato | Contenuto |
|----|--------|-------|-----------|
| **#6** | `claude/super-agent-strategy-ylp0fd` | **draft** | Fase 0 + Fase 1 completa |
| **#5** | `claude/multidisciplinary-tool-planning-ddtx6m` | draft | Solo documento di piano (da chiudere) |
| **#4** | `claude/confident-fermat-bXn8f` | draft | `.env.example` — **superseduta dalla PR #6**, da chiudere |

---

## 7. Avviso sicurezza Supabase (azione utente richiesta)

L'advisor Supabase segnala che **6 tabelle** di `super-agente-viaggi` hanno
**RLS disabilitata**: `istruzioni_agente`, `fonti_online`, `documenti`,
`conversazioni`, `dati_climatici`, `requisiti_visti`.

Non è stato applicato automaticamente perché abilitare RLS senza policy blocca
l'accesso da anon key. Quando vuoi risolvere:

```sql
-- Esegui DOPO aver definito le policy adeguate per ogni tabella.
ALTER TABLE public.istruzioni_agente ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fonti_online ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.documenti ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversazioni ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dati_climatici ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.requisiti_visti ENABLE ROW LEVEL SECURITY;
```

---

## 8. Come eseguire i test

```bash
pip install -r requirements.txt pytest anyio pytest-anyio
pytest tests/ -v
# Expected: 60 passed
```

Le chiamate OpenAI e LLM sono tutte mockate: non serve `OPENAI_API_KEY`.

---

## 9. Roadmap — prossime fasi

Vedi `ROADMAP.md` per il dettaglio completo.
