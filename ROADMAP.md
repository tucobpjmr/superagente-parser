# Roadmap — SuperAgente Viaggi

> Aggiornata: 2026-10-07 · sostituisce la roadmap del 2026-06-09

## Obiettivo

Un **assistente AI per gli operatori dell'agenzia**, integrato in
**VoyageDesk** (repo `tucobpjmr/TULLIO`), che risponde a casi complessi e
multidisciplinari (contratti, assicurazioni, fiscalità, visti, normativa UE)
citando le fonti. Sostituisce il vecchio "super-agente-viaggi".

## Decisioni prese

| Tema | Decisione |
|------|-----------|
| Utenti | Solo operatori dell'agenzia (non clienti finali) |
| Frontend | VoyageDesk — React 18 + Vite + Supabase (**non** Next.js) |
| Vecchio agente | Sostituito da questo servizio |

> ⚠️ Il progetto Supabase `vmxvnxsqfisucugcpqlc` (ex "super-agente-viaggi")
> oggi è il **database di produzione di VoyageDesk**. "Sostituire il vecchio
> agente" vale per la logica AI, **non** per quel database: non va toccato.
> La KB RAG resta su `pxtwdhhulobyrheioiex`.

## Architettura target

```
Operatore (VoyageDesk, login Supabase)
  │  JWT utente
  ▼
Edge Function VoyageDesk `assistente-ai`   ← verifica JWT + ruolo, rate limit
  │  PARSER_SHARED_SECRET (solo lato server)
  ▼
superagente-parser (Railway)
  ├─ /parse   → ingestione documenti
  ├─ /search  → retrieval multidisciplinare
  ├─ /answer  → sintesi con citazioni
  └─ /agent   → (nuovo) agente con tool + memoria
        │
        ▼
Supabase RAG (pxtwdhhulobyrheioiex)
```

Il secret del parser non arriva mai al browser.

## Stato attuale

| Componente | Stato |
|------------|-------|
| `/parse`, `/search`, `/answer` | ✅ completi, 90 test verdi |
| Golden set + `run_eval.py` | ✅ struttura, chunk_ids da annotare |
| `scripts/ingest.py` + RLS (PR #8) | 🟡 pronti in bozza, da mergiare |
| KB | 🔴 vuota → `/search` e `/answer` non trovano nulla |
| Integrazione VoyageDesk | 🔲 assente |
| CI GitHub Actions (parser) | 🔲 assente |

---

## Fase 1 — Sblocco (1 sessione)

1. Revisione e merge PR #8 (`ingest.py`, RLS).
   - ⚠️ Rivedere la policy `SELECT public` su `conversazioni`: con la anon key
     le chat sarebbero leggibili da chiunque. Restringere a `service_role`.
2. Railway: impostare `SUPABASE_URL` e `SUPABASE_SERVICE_KEY`.
3. Ingerire 10–20 documenti reali con `scripts/ingest.py`.
4. Smoke test `/search` e `/answer`, inclusa una domanda fuori KB
   (deve rispondere "nessuna fonte", non inventare).

**Fatto quando:** KB popolata e `/answer` restituisce citazioni corrette.

## Fase 2 — Qualità misurata (1–2 sessioni)

1. Annotare i `chunk_ids` reali in `eval/golden.jsonl`; misurare la baseline
   (recall@k, MRR, coverage discipline).
2. Tuning guidato dai numeri: `CHUNK_SIZE`, `top_k`, reranker, modelli.
3. CI GitHub Actions: `pytest` su ogni PR.

**Fatto quando:** baseline salvata e CI verde.

## Fase 3 — Da RAG ad agente (2–3 sessioni)

1. Nuovo `POST /agent` con tool calling:
   - `cerca_kb` (pipeline `/search` esistente)
   - `requisiti_visti`, `dati_climatici`, `fonti_online`
   - `istruzioni_agente` come prompt di sistema configurabile
2. Memoria conversazione su tabella `conversazioni` (per operatore).
3. Risposta in streaming (SSE).
4. Log di costi e token per richiesta.

**Fatto quando:** l'agente usa più tool in una risposta, con citazioni, in streaming.

## Fase 4 — Integrazione in VoyageDesk (2 sessioni, repo TULLIO)

1. Edge Function `assistente-ai`: verifica JWT, ruolo (Admin/Manager/Agent;
   escluso Driver), rate limit con l'infrastruttura già esistente, proxy al parser.
2. Modulo UI "Assistente": chat con citazioni cliccabili, storico.
3. Upload documenti nella KB dal pannello Admin (via `/parse` + INSERT).
4. Collegamenti utili: da una pratica/cliente aprire l'assistente con il
   contesto precompilato.

**Fatto quando:** un operatore fa una domanda da VoyageDesk e riceve
risposta citata, senza secret esposti.

## Fase 5 — Produzione e dismissione vecchio agente (1 sessione)

1. Dismettere il vecchio "super-agente-viaggi" (codice/route), dopo verifica
   che nessuna funzione di VoyageDesk ne dipenda.
2. Monitoraggio: errori, latenza, costi OpenAI per giorno.
3. Feedback 👍/👎 sulle risposte → nuove domande nel golden set.

## Debito tecnico

| # | Item | Priorità |
|---|------|----------|
| D1 | Rate limit distribuito (Redis) — solo se più repliche | Media |
| D2 | Streaming per documenti > 1000 chunk | Bassa |
| D3 | Job asincroni per documenti grandi | Bassa |
| D5 | Copertura test > 80% su `main.py` | Bassa |
| D6 | Pinning dipendenze (`pip-compile`) | Bassa |
