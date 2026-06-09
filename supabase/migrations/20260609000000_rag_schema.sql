-- Fase 0 — Schema RAG multidisciplinare
-- Idempotente: crea le tabelle se mancanti (install pulita) e le allinea
-- con ALTER additivi se esistono già (progetto super-agente-viaggi, che ha
-- documenti/document_chunks creati in una sessione precedente, nomi italiani).

create extension if not exists vector;

-- Install pulita: schema coerente con quello già presente in produzione.
create table if not exists documenti (
  id                uuid primary key default gen_random_uuid(),
  nome_file         text not null,
  tipo_file         text not null,
  categoria         text,
  descrizione       text,
  contenuto_testo   text,
  storage_path      text,
  dimensione_bytes  bigint,
  attivo            boolean default true,
  creato_il         timestamptz not null default now(),
  aggiornato_il     timestamptz not null default now()
);

create table if not exists document_chunks (
  id            uuid primary key default gen_random_uuid(),
  documento_id  uuid references documenti(id) on delete cascade,
  chunk_index   integer not null,
  contenuto     text not null,
  embedding     vector(1536),
  categoria     text,
  modulo        text,
  creato_il     timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Allineamento multidisciplinare (additivo, sicuro su tabelle esistenti)
-- ---------------------------------------------------------------------------

-- "modulo" resta come hint legacy; la classificazione vera è discipline[]
alter table documenti add column if not exists modulo text;
alter table documenti add column if not exists discipline text[] not null default '{}';
alter table documenti add column if not exists riassunto text;
-- SHA-256 del contenuto: abilita il caching anti-duplicazione (Fase 3)
alter table documenti add column if not exists content_hash text;
alter table documenti add column if not exists n_chunks integer;

create unique index if not exists documenti_content_hash_idx
  on documenti (content_hash) where content_hash is not null;

alter table document_chunks add column if not exists heading text;
alter table document_chunks add column if not exists discipline text[] not null default '{}';
-- riempiti dall'arricchimento LLM (Fase 1)
alter table document_chunks add column if not exists tags text[] not null default '{}';
alter table document_chunks add column if not exists entities jsonb;
-- Full-text italiano generato da heading + contenuto
alter table document_chunks add column if not exists fts tsvector
  generated always as (
    to_tsvector('italian', coalesce(heading, '') || ' ' || contenuto)
  ) stored;

-- Backfill: il modulo legacy diventa la prima disciplina
update documenti set discipline = array[modulo]
  where modulo is not null and discipline = '{}';
update document_chunks set discipline = array[modulo]
  where modulo is not null and discipline = '{}';

-- ---------------------------------------------------------------------------
-- Indici
-- ---------------------------------------------------------------------------

create unique index if not exists document_chunks_doc_chunk_idx
  on document_chunks (documento_id, chunk_index);

-- HNSW al posto del vecchio ivfflat: recall migliore e nessun training step;
-- la tabella è vuota quindi la sostituzione è a costo zero.
drop index if exists idx_document_chunks_embedding;
create index if not exists document_chunks_embedding_idx
  on document_chunks using hnsw (embedding vector_cosine_ops);

create index if not exists document_chunks_fts_idx
  on document_chunks using gin (fts);
create index if not exists document_chunks_discipline_idx
  on document_chunks using gin (discipline);
create index if not exists documenti_discipline_idx
  on documenti using gin (discipline);
create index if not exists document_chunks_documento_id_idx
  on document_chunks (documento_id);

-- RLS: document_chunks è già protetta (nessuna policy → solo service role).
-- NB: documenti resta con RLS disabilitata perché l'app legacy potrebbe
-- leggerla con anon key; abilitarla senza policy la bloccherebbe.
-- Decisione rimandata all'utente (vedi advisor Supabase).
alter table document_chunks enable row level security;
