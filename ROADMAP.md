# Roadmap — SuperAgente Parser

> Aggiornata: 2026-06-09 · branch `claude/super-agent-strategy-ylp0fd`

Obiettivo: un super agente capace di risolvere problemi complessi che
richiedono conoscenza multidisciplinare (es. "cliente annulla crociera per
malattia" → diritto contrattuale + assicurazioni + fiscalità IVA + normativa UE).

---

## Stato attuale

| Fase | Descrizione | Stato |
|------|-------------|-------|
| 0 | Fondamenta Supabase (schema RAG, `match_chunks` RRF) | ✅ Completa |
| 1.3 | Chunking contestuale (heading gerarchico, riassunto) | ✅ Completa |
| 1.1 | Classificazione multi-disciplina (tassonomia chiusa) | ✅ Completa |
| 1.2 | Estrazione entità "ponte" (norme, importi, date, organismi) | ✅ Completa |
| 2 | Retrieval multidisciplinare (`POST /search`) | 🔲 Da fare |
| 3.2 | Set di valutazione golden | 🔲 Da fare |
| 3.1 | Sintesi con citazioni (`POST /answer`) | 🔲 Da fare |

---

## Fase 2 — Retrieval multidisciplinare `POST /search`

**Questo è il cuore del potenziamento.** Stima: 2 sessioni.

### Prerequisiti

Aggiungere a Railway (e `.env.example`):
- `SUPABASE_URL=https://pxtwdhhulobyrheioiex.supabase.co`
- `SUPABASE_SERVICE_KEY=...` (dalla dashboard Supabase → API → service_role)

Dipendenze nuove da aggiungere a `requirements.txt`:
```
supabase==2.x      # client async python
```

### Endpoint

```
POST /search
Authorization: Bearer <PARSER_SHARED_SECRET>
Content-Type: application/json

{
  "domanda": "Il cliente annulla la crociera per malattia: ha diritto al rimborso?",
  "top_k": 8,
  "discipline": ["contrattualistica", "assicurazioni"]   // opzionale — boost, non filtro
}
```

### Pipeline interna

```
domanda
  │
  ▼
1. Decomposizione (LLM, gpt-4o-mini)
   → sotto-domande con discipline pertinenti
   es.:
     ① "rimborso contrattuale annullamento" → [contrattualistica, turismo]
     ② "copertura polizza malattia" → [assicurazioni]
     ③ "trattamento IVA penale annullamento" → [fiscalita]
  │
  ▼
2. Fan-out parallelo (asyncio.gather)
   Per ogni sotto-domanda:
     - embedding della sotto-domanda
     - chiamata match_chunks(embedding, testo, discipline, top_k*2)
  │
  ▼
3. Fusione RRF + deduplica UUID
  │
  ▼
4. Re-ranking (LLM-rerank sulla domanda originale completa)
   → ordina i top-k finali per rilevanza alla domanda originale
  │
  ▼
5. Risposta
   [
     {
       "id": "uuid",
       "contenuto": "...",
       "heading_path": "...",
       "discipline": ["fiscalita", "turismo"],
       "score": 0.089,
       "sotto_domanda": "trattamento IVA penale annullamento",
       "nome_file": "contratto.pdf",
       "documento_id": "uuid"
     }
   ]
```

### Dettagli implementativi

- **Decomposizione**: prompt JSON con `response_format=json_object`; parsing
  della lista `[{testo, discipline}]`; fallback a domanda singola senza
  decomposizione.
- **Query expansion leggera**: riformulazione IT/EN della query prima
  dell'embedding (i documenti normativi mescolano le due lingue).
- **Discipline come boost morbido**: passate a `match_chunks` come
  `filtro_discipline`, non come filtro rigido — già supportato dalla funzione
  SQL, non esclude chunk di altre discipline.
- **Nuovo file**: `search.py` (decomposizione + fan-out + fusione); logica
  DB in `db.py` (client `supabase-py`).
- **Auth**: stesso `PARSER_SHARED_SECRET` del `/parse`.

### Nuove env var di Fase 2

| Variabile | Default | Uso |
|-----------|---------|-----|
| `SUPABASE_URL` | — | URL progetto RAG |
| `SUPABASE_SERVICE_KEY` | — | Chiave service_role |
| `SEARCH_TOP_K` | `8` | Risultati finali post-rerank |
| `SEARCH_DECOMPOSE` | `true` | Attiva/disattiva decomposizione query |
| `SEARCH_RERANK` | `llm` | `llm` \| `none` |
| `RERANK_MODEL` | `gpt-4o-mini` | Modello per re-ranking |

---

## Fase 3.2 — Set di valutazione golden

**Da costruire IN PARALLELO alla Fase 2**, non dopo: i numeri guidano il
tuning di chunk_size, top_k, threshold RRF e scelta del reranker.

### Struttura

```
eval/
  golden.jsonl      — 20-30 domande con chunk_ids attesi e disciplina
  run_eval.py       — misura recall@k e MRR su ogni modifica della pipeline
  fixtures/
    sample.pdf      — PDF piccolo reale per test E2E
```

### Metriche

- **Recall@k**: quanti chunk "golden" compaiono nei top-k risultati?
- **MRR** (Mean Reciprocal Rank): quanto in alto compare il primo chunk golden?
- Misurate separatamente per query mono-disciplina e multi-disciplina.

### Domande golden da costruire (esempi)

| Domanda | Discipline attese |
|---------|-------------------|
| "Cliente annulla crociera per malattia — ha diritto al rimborso?" | contrattualistica, assicurazioni, turismo |
| "Come si gestisce l'IVA sulla penale di annullamento di un pacchetto?" | fiscalita, contrattualistica |
| "Quali documenti servono per un visto Schengen?" | visti-documenti, normativa-ue |
| "Il fornitore può modificare unilateralmente il prezzo del pacchetto?" | contrattualistica, normativa-ue |

---

## Fase 3.1 — Sintesi con citazioni `POST /answer`

Stima: 1 sessione. Può vivere nel microservizio Python o in Next.js (se si
preferisce streaming verso il browser).

### Endpoint

```
POST /answer
{ "domanda": "...", "top_k": 8 }
→ { "risposta": "...", "citazioni": [{chunk_id, nome_file, sezione, disciplina}] }
```

### Prompt di sintesi

I chunk vengono raggruppati per disciplina e passati all'LLM con istruzione di:
1. Integrare le informazioni provenienti da discipline diverse.
2. Segnalare esplicitamente i punti dove le discipline interagiscono o confliggono.
3. Citare la fonte con `[doc, sezione]` per ogni affermazione non ovvia.
4. Se le fonti sono insufficienti, dichiararlo invece di allucinare.

---

## Debito tecnico pendente

| # | Item | Priorità |
|---|------|----------|
| D1 | Rate limiting distribuito: l'implementazione in-memory non è condivisa tra più worker Railway. Fix: Redis (Upstash gratuito su Railway). | Media |
| D2 | Streaming response per documenti > 1000 chunk (risposta JSON > 10 MB). | Bassa |
| D3 | Job asincrono per parsing di documenti molto grandi (coda + polling `job_id`). | Bassa |
| D4 | Caching embedding: SHA-256 del contenuto → skip re-embedding di documenti identici. Colonna `content_hash` già nello schema. | Media (Fase 3) |
| D5 | `pytest-cov`: target > 80% di copertura su `main.py`. | Bassa |
| D6 | Dependency pinning con `pip-compile`. | Bassa |
| D7 | RLS su 6 tabelle Supabase (vedi avviso in HANDOFF). | Alta (sicurezza) |
| D8 | Chiudere PR #4 e PR #5 (supersedute da PR #6). | Bassa |

---

## Ordine di esecuzione consigliato per la prossima sessione

1. **Inizia da Fase 2** — è il cuore; la Fase 1 ha preparato esattamente i dati che servono.
2. **Costruisci il golden set** subito dopo il primo `POST /search` funzionante: hai bisogno dei numeri prima di fare tuning.
3. **Fase 3.1** per ultima — è il "bello" ma non misurabile senza i numeri della Fase 3.2.
4. Affronta il debito D7 (RLS) quando conosci le policy di accesso dell'app Next.js.
