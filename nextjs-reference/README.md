# Next.js — Fase 3 Reference

File da copiare nel progetto Next.js di SuperAgente Viaggi:

- `app/api/parse-file/route.ts` → sostituisce l'attuale route handler.

## Variabili d'ambiente richieste (`.env.local`)

```
PYTHON_PARSER_URL=https://superagente-parser-production.up.railway.app
PARSER_SHARED_SECRET=<stesso valore impostato su Railway>
NEXT_PUBLIC_SUPABASE_URL=<già presente>
SUPABASE_SERVICE_ROLE_KEY=<già presente>
```

## Dipendenze npm

```
npm i @supabase/supabase-js
```

## Contratto frontend → /api/parse-file

`POST multipart/form-data` con i campi:

| campo          | tipo   | obbligatorio | note                                          |
| -------------- | ------ | ------------ | --------------------------------------------- |
| `file`         | File   | sì           | estensioni supportate dal parser Python       |
| `modulo`       | string | sì           | es. `viaggi`, `pratiche`, ecc.                |
| `categoria`    | string | sì           | es. `contratti`, `cataloghi`                  |
| `documento_id` | string | no           | UUID; se assente viene generato lato server   |

## Risposta

```json
{
  "ok": true,
  "documento_id": "uuid",
  "n_chunks": 12,
  "metadata": { "file": "...", "n_chunks": 12, "embedding_dim": 1536, ... }
}
```

## Errori gestiti

- `400` body multipart mancante o campi obbligatori vuoti
- `500` parser non configurato (env mancanti) o errore INSERT Supabase
- `502` errore di rete / risposta non valida dal parser
- `504` timeout parser (>60s)
