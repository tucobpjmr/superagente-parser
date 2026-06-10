-- Fase 0 — Ricerca ibrida (densa + full-text) con Reciprocal Rank Fusion.
-- Le discipline sono un BOOST morbido (x1.5), mai un filtro rigido:
-- una query multidisciplinare non deve perdere fonti fuori silos.

create or replace function match_chunks(
  query_embedding   vector(1536),
  query_text        text,
  filtro_discipline text[] default null,
  match_count       int default 8,
  rrf_k             int default 50
)
returns table (
  id           uuid,
  documento_id uuid,
  chunk_index  int,
  contenuto    text,
  heading      text,
  discipline   text[],
  categoria    text,
  score        double precision
)
language sql
stable
as $$
  with dense as (
    select c.id,
           row_number() over (order by c.embedding <=> query_embedding) as rank
    from document_chunks c
    where c.embedding is not null
    order by c.embedding <=> query_embedding
    limit greatest(match_count * 4, 50)
  ),
  sparse as (
    select c.id,
           row_number() over (
             order by ts_rank_cd(c.fts, websearch_to_tsquery('italian', query_text)) desc
           ) as rank
    from document_chunks c
    where coalesce(query_text, '') <> ''
      and c.fts @@ websearch_to_tsquery('italian', query_text)
    limit greatest(match_count * 4, 50)
  ),
  fused as (
    select coalesce(d.id, s.id) as id,
           coalesce(1.0 / (rrf_k + d.rank), 0)
         + coalesce(1.0 / (rrf_k + s.rank), 0) as rrf
    from dense d
    full outer join sparse s using (id)
  )
  select c.id,
         c.documento_id,
         c.chunk_index,
         c.contenuto,
         c.heading,
         c.discipline,
         c.categoria,
         f.rrf * case
                   when filtro_discipline is not null
                    and c.discipline && filtro_discipline
                   then 1.5
                   else 1.0
                 end as score
  from fused f
  join document_chunks c on c.id = f.id
  order by score desc
  limit match_count;
$$;
