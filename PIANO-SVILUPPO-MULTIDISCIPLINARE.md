# Piano di sviluppo — Capacità multidisciplinari

> Obiettivo: potenziare SuperAgente affinché risolva problemi la cui soluzione
> richiede conoscenza proveniente da **più discipline contemporaneamente**
> (es. una cancellazione viaggio che tocca diritto contrattuale, fiscalità,
> assicurazioni e regolamenti di settore).
>
> Stato analisi: 2026-06-09 · branch `claude/multidisciplinary-tool-planning-ddtx6m`

---

## 1. Diagnosi: perché oggi lo strumento NON è multidisciplinare

### 1.1 Architettura attuale

```
Upload → POST /parse → Markitdown → chunking → embedding OpenAI
                                                    ↓
                              chunk taggati con UN SOLO modulo + categoria
                                                    ↓
                                  (previsto) Supabase pgvector
```

Il parser è un servizio di **sola ingestion**: riceve un documento, lo spezza,
genera embedding e restituisce chunk etichettati. Tutta la parte di
*risoluzione dei problemi* (retrieval + ragionamento) è demandata altrove e
oggi non esiste.

### 1.2 Limiti strutturali individuati

| # | Limite | Dove | Effetto sul multidisciplinare |
|---|--------|------|-------------------------------|
| L1 | **Tassonomia mono-modulo**: ogni chunk ha esattamente un `modulo` (stringa libera fornita dal caller) | `main.py` (`/parse`), schema risposta | La conoscenza è in silos. Un documento su "IVA dei pacchetti turistici" finisce o in `fiscalita` o in `viaggi`, mai in entrambi. Una query cross-dominio che filtra per modulo perde metà delle fonti. |
| L2 | **Nessun arricchimento semantico**: i chunk non hanno tag, entità, riferimenti normativi, riassunti | `chunker.py`, `main.py` | Impossibile collegare documenti di discipline diverse che parlano della stessa entità (stessa legge, stesso istituto giuridico, stessa destinazione). |
| L3 | **Chunk senza contesto documentale**: l'embedding è calcolato sul solo testo del chunk (più heading) | `chunker.py`, `embeddings.py` | Un chunk "Art. 5 — il fornitore rimborsa entro 14 giorni" senza il contesto "Condizioni generali pacchetti turistici" ha un embedding ambiguo → retrieval cross-dominio scadente. |
| L4 | **Nessun endpoint di retrieval**: il servizio non sa cercare, solo ingerire | tutto il repo | La logica multidisciplinare (decomposizione query, fan-out, fusione) non ha dove vivere. |
| L5 | **Schema dati assente**: il progetto Supabase attivo (`tullio`) non ha né `documenti` né `document_chunks` né l'estensione pgvector | Supabase | Il RAG previsto da README/HANDOFF non è ancora deployabile end-to-end. |
| L6 | **Solo ricerca densa**: niente full-text/BM25 ibrido | — | Termini tecnici esatti (numeri di legge, sigle: "Dlgs 62/2024", "ATOL") sono il pane delle query multidisciplinari e la ricerca solo-vettoriale li gestisce male. |

**Conclusione**: il collo di bottiglia non è la qualità del parsing ma il
modello dati (silos mono-modulo) e l'assenza dello strato di retrieval e
orchestrazione.

---

## 2. Architettura target

```
                         INGESTION (potenziata)
file → /parse → markdown → chunking contestuale → arricchimento LLM
                                                   (discipline[], tag, entità)
                                                          ↓
                                              embedding + tsvector
                                                          ↓
                                            Supabase: documenti, document_chunks
                                                          ↓
                         RETRIEVAL (nuovo)
domanda → /search → decomposizione in sotto-domande per disciplina
                  → retrieval ibrido parallelo (denso + BM25, cross-modulo)
                  → fusione RRF + re-ranking
                  → chunk multidisciplinari ordinati
                                                          ↓
                         SINTESI (nuovo, fase 3)
        → /answer → LLM con citazioni che integra le discipline
```

Decisione architetturale consigliata: **il retrieval vive in questo
microservizio Python**, non in Next.js. Motivi: la logica embedding è già qui,
Python ha l'ecosistema migliore per re-ranking/valutazione, e Next.js resta un
semplice proxy autenticato (pattern già in uso con `PARSER_SHARED_SECRET`).

---

## 3. Fasi di sviluppo

### Fase 0 — Fondamenta (prerequisiti, ~1 sessione)

Chiude il debito della roadmap HANDOFF e crea lo schema dati mancante.

- [ ] Creare su Supabase: estensione `vector`, tabelle `documenti` e
      `document_chunks` con colonna `embedding vector(1536)`, indice HNSW,
      colonna `fts tsvector` generata (config `italian`) con indice GIN.
- [ ] **Cambio chiave dello schema**: `modulo text` → `discipline text[]`
      (con indice GIN). Retrocompatibilità: il `modulo` passato al parse resta
      la prima disciplina.
- [ ] Funzione SQL `match_chunks(query_embedding, query_text, filtro_discipline)`
      per ricerca ibrida con Reciprocal Rank Fusion direttamente in Postgres.
- [ ] `.env.example`, `healthcheckPath` → `/ready`, aggiornamento `markitdown`.

### Fase 1 — Ingestion multidisciplinare (~1-2 sessioni)

Risolve L1, L2, L3, L6. Tutto in questo repo.

1. **Classificazione multi-disciplina per chunk** (`enrichment.py`, nuovo)
   - Una chiamata a un modello economico (es. `gpt-4o-mini` o equivalente)
     per batch di chunk: restituisce `discipline: string[]` da una tassonomia
     controllata e configurabile (`DISCIPLINE_TAXONOMY` env / file YAML, es.:
     fiscalita, contrattualistica, assicurazioni, trasporti, normativa-ue,
     privacy, contabilita, ...), più `tags: string[]` liberi.
   - Il `modulo` del form diventa un *hint*, non più l'etichetta esclusiva.
   - Flag `ENRICHMENT_ENABLED` per disattivarlo (fallback: comportamento odierno).

2. **Estrazione entità e riferimenti incrociati**
   - Nello stesso prompt di classificazione: estrarre riferimenti normativi
     (leggi, articoli, direttive), importi, date, organismi. Salvati in
     `entities jsonb` sul chunk.
   - Questi sono i "ponti" tra discipline: due chunk di moduli diversi che
     citano la stessa norma sono collegabili a costo zero in query.

3. **Chunking contestuale** (modifica `chunker.py` + `main.py`)
   - Generare un riassunto del documento (1 chiamata LLM) e anteporre a ogni
     chunk, prima dell'embedding, una riga di contesto:
     `"[Doc: <titolo> — <riassunto 1 frase> — Sezione: <heading>]"`.
   - Il testo salvato resta pulito; solo l'input dell'embedding è arricchito.
     (Tecnica "contextual retrieval": +30-50% di precisione tipica su corpus
     eterogenei, esattamente il caso multidisciplinare.)
   - Includere la gerarchia completa degli heading (H1 > H2 > H3), non solo
     l'ultimo: il chunker attuale conserva un solo livello.

4. **Risposta `/parse` estesa**
   - Ogni chunk esce con `discipline`, `tags`, `entities`, `contesto`.
   - `metadata` include `riassunto_documento`.

### Fase 2 — Retrieval multidisciplinare (~2 sessioni)

Risolve L4. Il cuore del potenziamento.

1. **`POST /search`** (nuovo endpoint, stessa auth shared-secret)
   - Input: `{ domanda, top_k, discipline? }`.
   - Pipeline:
     a. **Decomposizione**: un LLM analizza la domanda e produce 1-N
        sotto-domande, ciascuna con le discipline pertinenti
        (es. "cliente annulla crociera per malattia" →
        ① rimborso contrattuale [contrattualistica, trasporti]
        ② copertura polizza [assicurazioni]
        ③ trattamento IVA della penale [fiscalita]).
     b. **Fan-out parallelo**: per ogni sotto-domanda, ricerca ibrida
        (embedding + full-text) con `asyncio.gather`, filtro morbido sulle
        discipline (boost, non esclusione — mai rifiltrare in silos rigidi).
     c. **Fusione RRF** dei risultati + deduplica.
     d. **Re-ranking** finale (cross-encoder locale o LLM-rerank) sulla
        domanda originale completa.
   - Output: chunk ordinati con `sotto_domanda`, `discipline`, `score`,
     pronti per il prompt di sintesi.
   - Accesso DB: client `supabase-py` o `asyncpg` (nuova dipendenza,
     `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` in env).

2. **Query expansion leggera**: riformulazione multilingua IT/EN della query
   (i documenti normativi/tecnici mescolano le due lingue).

### Fase 3 — Sintesi e qualità (~1-2 sessioni)

1. **`POST /answer`** (opzionale, può stare in Next.js se si preferisce lo
   streaming verso il browser): prompt di sintesi che riceve i chunk
   raggruppati per disciplina e produce una risposta integrata con citazioni
   `[doc, sezione]`, segnalando esplicitamente i punti in cui le discipline
   interagiscono o confliggono.
2. **Set di valutazione**: 20-30 domande multidisciplinari "golden" con i
   chunk attesi; script `eval/run_eval.py` che misura recall@k e MRR su ogni
   modifica della pipeline. Senza questo, ogni tuning è alla cieca.
3. **Caching**: hash SHA-256 del contenuto → skip re-embedding di documenti
   identici (già in roadmap HANDOFF, qui diventa importante perché
   l'arricchimento LLM ha un costo per documento).

---

## 4. Nuove variabili d'ambiente previste

| Variabile | Fase | Default | Uso |
|-----------|:---:|---------|-----|
| `ENRICHMENT_ENABLED` | 1 | `true` | Attiva classificazione/entità LLM |
| `ENRICHMENT_MODEL` | 1 | `gpt-4o-mini` | Modello economico per enrichment |
| `DISCIPLINE_TAXONOMY` | 1 | built-in | Lista discipline (csv o path YAML) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | 2 | — | Accesso DB per `/search` |
| `SEARCH_TOP_K` | 2 | `8` | Risultati finali post-rerank |
| `RERANKER` | 2 | `llm` | `llm` \| `none` (cross-encoder in futuro) |

---

## 5. Rischi e mitigazioni

- **Costo/latenza enrichment**: +1-2 chiamate LLM per documento in ingestion.
  Mitigazione: batch, modello mini, flag di disattivazione, caching per hash.
- **Tassonomia che deriva**: discipline libere → proliferazione di etichette.
  Mitigazione: tassonomia chiusa e versionata; l'LLM sceglie solo dalla lista.
- **Progetto Supabase**: le tabelle RAG non esistono ancora (L5) e il progetto
  `super-agente-viaggi` risulta INACTIVE; chiarire quale progetto ospiterà il
  RAG prima della Fase 0.
- **Migrazione dati**: se esistono già chunk mono-modulo in produzione,
  backfill `discipline = ARRAY[modulo]` + ri-classificazione lazy.

---

## 6. Ordine di esecuzione consigliato

1. **Fase 0** subito (sblocca tutto, basso rischio).
2. **Fase 1.3 (chunking contestuale)** prima di 1.1: migliora il retrieval
   anche senza classificazione e non richiede schema nuovo.
3. **Fase 1.1/1.2** (discipline multiple + entità).
4. **Fase 2** (`/search` con decomposizione + ibrido + RRF).
5. **Fase 3.2 (valutazione)** in parallelo alla Fase 2 — i numeri guidano
   il tuning di top_k, soglie e reranker.
6. **Fase 3.1 (`/answer`)** per ultima.
