-- Fase 0 — Schema RAG multidisciplinare
-- Tabelle per documenti e chunk con embedding pgvector, discipline multiple
-- e ricerca full-text italiana.

create extension if not exists vector;

-- Documento parent: un record per file ingerito.
create table if not exists documenti (
  id            uuid primary key default gen_random_uuid(),
  filename      text not null,
  -- "modulo" legacy: resta come hint del caller; la classificazione vera è discipline[]
  modulo        text,
  discipline    text[] not null default '{}',
  categoria     text,
  riassunto     text,
  -- SHA-256 del contenuto: abilita il caching anti-duplicazione (Fase 3)
  content_hash  text unique,
  n_chunks      integer,
  created_at    timestamptz not null default now()
);

create table if not exists document_chunks (
  id            uuid primary key default gen_random_uuid(),
  documento_id  uuid references documenti(id) on delete cascade,
  chunk_index   integer not null,
  contenuto     text not null,
  heading       text,
  discipline    text[] not null default '{}',
  categoria     text,
  -- riempiti dall'arricchimento LLM (Fase 1)
  tags          text[] not null default '{}',
  entities      jsonb,
  embedding     vector(1536),
  fts           tsvector generated always as (
                  to_tsvector('italian', coalesce(heading, '') || ' ' || contenuto)
                ) stored,
  created_at    timestamptz not null default now(),
  unique (documento_id, chunk_index)
);

-- Ricerca densa (cosine) — HNSW
create index if not exists document_chunks_embedding_idx
  on document_chunks using hnsw (embedding vector_cosine_ops);

-- Ricerca sparsa (full-text italiano)
create index if not exists document_chunks_fts_idx
  on document_chunks using gin (fts);

-- Filtro/boost per disciplina
create index if not exists document_chunks_discipline_idx
  on document_chunks using gin (discipline);
create index if not exists documenti_discipline_idx
  on documenti using gin (discipline);

create index if not exists document_chunks_documento_id_idx
  on document_chunks (documento_id);

-- RLS attiva senza policy: l'accesso avviene solo via service role (parser),
-- che bypassa RLS. Nessun accesso anon/authenticated diretto.
alter table documenti enable row level security;
alter table document_chunks enable row level security;
