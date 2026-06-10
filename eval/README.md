# Eval — Set di valutazione golden (Fase 3.2)

Misura la qualità del retrieval di `POST /search` su un set fisso di domande.
I numeri guidano il tuning di `chunk_size`, `top_k`, threshold RRF e scelta
del reranker: vanno rimisurati a ogni modifica della pipeline.

## Uso

```bash
python eval/run_eval.py \
  --base-url https://<parser>.railway.app \
  --secret "$PARSER_SHARED_SECRET" \
  --top-k 8
```

## Metriche

- **Recall@k** — quanti riferimenti golden compaiono nei top-k risultati
- **MRR** — reciproco del rank del primo risultato golden (quanto in alto compare)
- **Discipline coverage** — frazione delle discipline attese effettivamente
  presenti nei top-k (proxy della capacità multidisciplinare)

Riportate separatamente per domande **mono-** e **multi-disciplina**.

## `golden.jsonl`

Una riga JSON per domanda:

```json
{
  "id": "g01",
  "domanda": "Il cliente annulla la crociera per malattia: ha diritto al rimborso?",
  "discipline_attese": ["contrattualistica", "assicurazioni", "turismo"],
  "multi": true,
  "chunk_ids": ["<uuid>", "..."],
  "substrings": ["annullament", "rimborso", "polizza"]
}
```

Due criteri di match, in ordine di priorità:

1. **`chunk_ids`** — UUID dei chunk attesi. È il criterio esatto: **da
   compilare dopo l'ingestion dei documenti reali** (oggi la KB è vuota).
   Per annotarli: esegui la domanda su `/search`, ispeziona i risultati e
   copia gli id dei chunk davvero pertinenti.
2. **`substrings`** — criterio surrogato: un risultato conta come golden se
   il contenuto contiene (case-insensitive) una delle stringhe. Permette di
   avere numeri indicativi da subito, prima dell'annotazione manuale.

> Nota: finché `chunk_ids` è vuoto, recall@k usa il surrogato con
> denominatore 1 (esiste almeno un chunk pertinente?) — è un proxy ottimista.
> L'annotazione dei chunk_ids reali rende i numeri affidabili.

## Workflow consigliato

1. Ingerisci i documenti reali della KB (`POST /parse` → INSERT su Supabase)
2. Primo run con i surrogati → baseline indicativa
3. Annota i `chunk_ids` per ogni domanda
4. Rimisura a ogni modifica di pipeline (chunking, top_k, rrf_k, reranker)
