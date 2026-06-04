# Handoff & Roadmap

Documento di passaggio di consegne per riprendere il lavoro sul progetto.
Aggiornato al: **2026-06-04**

---

## Stato corrente

**Branch attivo**: `claude/travel-agent-architecture-gFUUz`
**PR aperta**: [#2 — perf: parallelizza batch embedding, ottimizza chunker, timeout OCR](https://github.com/tucobpjmr/superagente-parser/pull/2) (draft)

### Cosa è stato fatto in questa sessione

Analisi completa del codebase con identificazione di **bug, vulnerabilità di sicurezza e problemi di performance**. Applicate **solo le ottimizzazioni di performance** (per scelta esplicita dell'utente):

| ID | File | Modifica |
|----|------|----------|
| PERF-1 | `embeddings.py` | Batch OpenAI ora paralleli via `asyncio.gather` (era loop sequenziale) |
| PERF-2/3 | `chunker.py` | `_split_by_words` riceve `List[str]` invece di `str` → un solo `.split()` per sezione |
| PERF-4 | `chunker.py` | Merge loop usa lista + `"\n\n".join()` finale (no concatenazione stringhe) |
| PERF-5 | `main.py` | Markitdown in `run_in_executor` con timeout 60s + logging timing per fase |
| PERF-6 | `requirements.txt` | Rimosso `python-dotenv` (non usato) |
| infra | `.gitignore` | Aggiunto (escludeva `__pycache__`) |

Test esistenti: **6/6 passano** dopo le modifiche.

### Stima impatto

- Documento con 300 chunk: tempo embedding da ~3s → ~1s (asintotico al batch più lento)
- Documenti grandi: chunking ~2× più veloce (un solo `.split()` per sezione)
- OCR su PDF problematici: non blocca più il worker indefinitamente (timeout 60s)

---

## Cosa NON è stato applicato (deliberatamente)

L'analisi ha identificato altre criticità che restano aperte. Sono raggruppate per priorità nella roadmap sotto.

---

## Roadmap

### 🔴 Priorità 1 — Sicurezza & correttezza (critica)

#### R1.1 — Size check prima della lettura completa in RAM
**File**: `main.py:81-87`
**Problema**: `await file.read()` carica tutto in RAM **prima** del check `MAX_UPLOAD_MB`. Un attacco con file enorme causa OOM del container.
**Fix**: leggere in stream con limite progressivo, oppure leggere chunked finché si supera la soglia.

#### R1.2 — Validazione input form
**File**: `main.py:61-63`
**Problema**: `modulo`, `categoria`, `documento_id` non hanno `max_length` né validazione formato. `documento_id` dovrebbe essere UUID.
**Fix**:
```python
from pydantic import Field
from typing import Annotated
modulo: Annotated[str, Form(..., max_length=100, pattern=r"^[a-z_]+$")]
documento_id: Annotated[Optional[str], Form(pattern=r"^[0-9a-f-]{36}$")] = None
```

#### R1.3 — Rate limiting su `/parse`
**File**: `main.py:58`
**Problema**: Spam di richieste può esaurire quota OpenAI e abbattere il servizio.
**Fix**: aggiungere `slowapi` con limite per IP (es. 10 req/min).

#### R1.4 — Bug merge finale chunker
**File**: `chunker.py:105-106`
**Problema**: l'ultimo buffer viene aggiunto a `merged` anche se ha `< min_chunk_words`, senza tentare un ulteriore merge con `merged[-1]`.
**Fix**: prima del `merged.append(buffer)` finale, controllare e fondere con il precedente.

### 🟠 Priorità 2 — Robustezza

#### R2.1 — Sanitizzazione `file.filename`
**File**: `main.py:89, 143`
**Problema**: filename arbitrario finisce nei log e nella response → logging injection / path traversal.
**Fix**: `safe_name = Path(file.filename or "").name`.

#### R2.2 — Validazione MIME type oltre all'estensione
**File**: `main.py:73-78`
**Problema**: un `.exe` rinominato `.pdf` passa il check.
**Fix**: aggiungere `python-magic` e verificare il content-type reale dal file header.

#### R2.3 — CORS troppo permissivo sugli header
**File**: `main.py:47`
**Problema**: `allow_headers=["*"]`.
**Fix**: restringere a `["Content-Type", "Authorization"]`.

#### R2.4 — Healthcheck Dockerfile senza timeout
**File**: `Dockerfile:36`
**Problema**: `urllib.request.urlopen()` senza timeout può bloccare il healthcheck.
**Fix**: aggiungere `timeout=5` alla chiamata.

### 🟡 Priorità 3 — Qualità del codice

#### R3.1 — Test coverage
Mancano test per:
- `main.py` (endpoint, auth, size limit, extension check) → usare `fastapi.testclient.TestClient`
- `embeddings.py` (batch logic, retry mock) → mock `_embed_batch` con `unittest.mock.AsyncMock`

#### R3.2 — Configurazioni hardcoded
**File**: `embeddings.py:16` (`BATCH_SIZE`), `main.py:111` (`chunk_size=500, overlap=50`)
**Fix**: spostare a env variables con default.

#### R3.3 — Singleton client OpenAI con lock asyncio
**File**: `embeddings.py:18-28`
Pattern singleton attualmente è race-prone tra coroutine. Impatto reale basso ma per pulizia usare `asyncio.Lock`.

#### R3.4 — Log strutturato
Sostituire logging classico con formato JSON (`python-json-logger`) per ingestione in tool di observability.

---

## Come riprendere

1. **Verifica stato PR**:
   ```bash
   git checkout claude/travel-agent-architecture-gFUUz
   git pull
   ```
   Controlla `https://github.com/tucobpjmr/superagente-parser/pull/2` per merge/commenti.

2. **Per applicare la roadmap**: scegliere una priorità (1, 2, o 3) e aprire un nuovo branch dedicato dal `main` aggiornato, es. `claude/security-input-validation`. Non accumulare cambiamenti eterogenei sullo stesso branch.

3. **Validazione locale prima del push**:
   ```bash
   python -m pytest tests/ -v
   uvicorn main:app --reload     # test manuale endpoint
   ```

4. **PR convention**: titolo prefissato con tipo (`perf:`, `fix:`, `feat:`, `chore:`). Body con sezione "Sommario" e "Piano di test".

---

## Domande aperte per il prossimo Claude / utente

1. Vale la pena introdurre **caching degli embedding** (es. Redis con hash del testo come chiave)? Documenti riprocessati paghino due volte OpenAI.
2. Si vuole **streamare la risposta** (chunk-by-chunk) invece di buffer JSON unico? Utile per documenti con 1000+ chunk.
3. Il numero di **2 worker Uvicorn** è adeguato per il throughput previsto? Dipende dal piano Railway.
4. Si vuole esporre **metriche Prometheus** (`prometheus-fastapi-instrumentator`) per dashboarding?
