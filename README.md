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

Ogni chunk include anche `heading_path` (gerarchia completa H1 > H2 > H3) e i
metadata espongono `riassunto_documento`.

### Contextual retrieval

Prima dell'embedding, a ogni chunk viene anteposta una riga di contesto
(titolo del documento + riassunto di una frase generato via LLM + breadcrumb di
sezione): `[Doc: <file> — <riassunto> — Sezione: <H1 > H2>]`. Migliora il
retrieval cross-dominio perché un chunk isolato porta con sé il tema e la
collocazione del documento. **Il testo salvato in `contenuto` resta pulito**:
solo l'input dell'embedding è arricchito.

Disattivabile con `CONTEXTUAL_EMBEDDING=false` (torna all'embedding del solo
contenuto del chunk) o `ENRICHMENT_ENABLED=false` (salta la sola chiamata LLM
del riassunto, mantenendo titolo + sezione).

### Caching per content_hash

Prima di OCR + embedding, `/parse` calcola lo **SHA-256 dei byte del file** e
cerca su Supabase un documento con lo stesso `content_hash`. In caso di hit i
chunk (con embedding) vengono riletti da `document_chunks` e restituiti senza
alcuna chiamata OpenAI (`metadata.cached: true`, `documento_id_cache` con
l'id del documento già ingerito — utile al caller per evitare un duplicato).

Il caller deve salvare `metadata.content_hash` nella colonna
`documenti.content_hash` all'INSERT, altrimenti la cache non avrà mai hit.
Best-effort: errori o Supabase non configurato → pipeline completa.
Disattivabile con `PARSE_CACHE=false`. Dopo modifiche alla pipeline
(CHUNK_SIZE, modello embedding, contextual retrieval) re-ingerire i documenti:
la cache restituisce i chunk così come furono generati.

### Classificazione multi-disciplina ed entità

Ogni chunk viene classificato via LLM (batch paralleli, stesso modello
economico del riassunto) con:

- `discipline`: discipline pertinenti scelte da una **tassonomia chiusa**
  (configurabile con `DISCIPLINE_TAXONOMY`, CSV). Il `modulo` del form resta
  sempre la **prima** disciplina; la classificazione aggiunge le altre.
- `tags`: parole chiave libere (max 8, minuscole).
- `entities`: entità "ponte" tra discipline — riferimenti normativi, importi,
  date, organismi (`jsonb` su Supabase). Due chunk di moduli diversi che
  citano la stessa norma diventano collegabili in query.

La classificazione gira **in parallelo all'embedding** (niente latenza extra
nel caso tipico) e degrada senza bloccare l'ingestion: in caso di errore o
timeout (`CLASSIFY_TIMEOUT`) i chunk escono con `discipline=[modulo]`,
`tags=[]`, `entities=null`. Disattivabile con `ENRICHMENT_ENABLED=false`.

### `POST /search`
Retrieval multidisciplinare: decomposizione + fan-out ibrido + RRF + re-ranking.

**JSON body**:
```json
{
  "domanda": "Il cliente annulla la crociera per malattia: ha diritto al rimborso?",
  "top_k": 8,
  "discipline": ["contrattualistica", "assicurazioni"]
}
```

`top_k` e `discipline` sono opzionali; `discipline` agisce come **boost morbido**
(mai filtro rigido), sommandosi a quelle individuate dalla decomposizione.

**Pipeline**:
1. **Decomposizione** (LLM, disattivabile con `SEARCH_DECOMPOSE=false`): la domanda viene scomposta in sotto-domande, ognuna con le discipline pertinenti dalla tassonomia chiusa e una riformulazione EN per l'embedding (i documenti normativi mescolano IT/EN)
2. **Fan-out parallelo**: per ogni sotto-domanda, embedding + RPC `match_chunks` su Supabase (ibrida dense+FTS con RRF interno e boost ×1.5 per disciplina)
3. **Fusione RRF** + deduplica UUID tra sotto-domande
4. **Re-ranking** LLM sulla domanda originale completa (`SEARCH_RERANK=llm|none`)

**Risposta**:
```json
{
  "domanda": "...",
  "sotto_domande": [{"testo": "copertura polizza malattia", "testo_en": "illness insurance coverage", "discipline": ["assicurazioni"]}],
  "n_candidati": 14,
  "risultati": [
    {
      "id": "uuid",
      "documento_id": "uuid",
      "contenuto": "...",
      "heading": "Condizioni generali > Art. 12 — Recesso",
      "discipline": ["contrattualistica"],
      "score": 0.089,
      "rrf_score": 0.032,
      "sotto_domanda": "rimborso contrattuale annullamento",
      "nome_file": "contratto.pdf"
    }
  ],
  "duration_s": 1.42,
  "errors": null
}
```

Richiede env `SUPABASE_URL` e `SUPABASE_SERVICE_KEY`. Auth: stesso `PARSER_SHARED_SECRET` di `/parse`.

| Env var | Default | Uso |
|---------|---------|-----|
| `SUPABASE_URL` | — | URL progetto RAG (obbligatoria per `/search`) |
| `SUPABASE_SERVICE_KEY` | — | Chiave service_role (obbligatoria per `/search`) |
| `SEARCH_TOP_K` | `8` | Risultati finali post-rerank |
| `SEARCH_DECOMPOSE` | `true` | Attiva/disattiva decomposizione query |
| `SEARCH_RERANK` | `llm` | `llm` \| `none` |
| `DECOMPOSE_MODEL` | `gpt-4o-mini` | Modello per decomposizione |
| `RERANK_MODEL` | `gpt-4o-mini` | Modello per re-ranking |
| `SEARCH_HTTP_TIMEOUT` | `15` | Timeout (s) chiamate Supabase |
| `SEARCH_LLM_TIMEOUT` | `20` | Timeout (s) chiamate LLM |

### `POST /answer`
Sintesi con citazioni: ricerca + risposta integrata multidisciplinare.

**JSON body**:
```json
{
  "domanda": "Il cliente annulla la crociera per malattia: ha diritto al rimborso?",
  "top_k": 8,
  "discipline": ["contrattualistica", "assicurazioni"]
}
```

**Pipeline**:
1. Recupera top-k chunk via `search_pipeline` (decompose + RRF + rerank)
2. Raggruppa i chunk per disciplina principale, numerati `[1]`, `[2]`, …
3. Sintetizza con LLM (`ANSWER_MODEL`, default `gpt-4o`) — il prompt impone:
   integrare le discipline, segnalare interazioni/conflitti, citare inline
   con `[n]`, dichiarare insufficienza delle fonti invece di allucinare
4. Restituisce risposta + array `citazioni` allineato agli `[n]` inline

**Risposta**:
```json
{
  "domanda": "...",
  "risposta": "Il viaggiatore ha diritto al rimborso integrale [1]; la polizza copre l'annullamento se documentato [2]. Sul piano fiscale ...",
  "citazioni": [
    {"n": 1, "chunk_id": "uuid", "documento_id": "uuid", "nome_file": "contratto.pdf", "sezione": "Art. 12 — Recesso", "discipline": ["contrattualistica"], "disciplina": "contrattualistica"},
    {"n": 2, "chunk_id": "uuid", "nome_file": "polizza.pdf", "sezione": "Coperture", "discipline": ["assicurazioni"], "disciplina": "assicurazioni"}
  ],
  "retrieval": {"sotto_domande": [...], "n_candidati": 14, "errors": null},
  "duration_s": 4.21
}
```

| Env var | Default | Uso |
|---------|---------|-----|
| `ANSWER_MODEL` | `gpt-4o` | Modello di sintesi (alza qualità rispetto al rerank) |
| `ANSWER_LLM_TIMEOUT` | `45` | Timeout (s) sintesi |
| `ANSWER_CHUNK_CHARS` | `1500` | Troncamento per chunk nel prompt |
| `ANSWER_MAX_CITATIONS` | `12` | Cap sui chunk passati al sintetizzatore |

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
4. `railway up`
5. Copia l'URL pubblico → mettilo in `PYTHON_PARSER_URL` lato Next.js

## Test

```bash
pytest tests/
```

## OCR

OCR italiano + inglese inclusi nel Dockerfile (`tesseract-ocr-ita`, `tesseract-ocr-eng`).
Markitdown attiva l'OCR automaticamente quando il PDF non ha layer testuale.
