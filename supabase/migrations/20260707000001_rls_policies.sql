-- D7 (Fase B) — RLS: abilita e definisce policy su tutte le tabelle pubbliche.
--
-- Contesto accessi (al 2026-07-07):
--   • Parser service (Railway)        → service_role key → bypass RLS automatico
--   • scripts/ingest.py               → service_role key → bypass RLS automatico
--   • App Next.js / browser           → anon key oppure authenticated user
--     (non ancora determinato con certezza; policy permissive in lettura
--      per non bloccare nulla; restringere con auth utente quando si integra
--      Supabase Auth nell'app)
--
-- Strategia:
--   • documenti / document_chunks: lettura pubblica (SELECT), scrittura solo
--     via service_role (che bypassa RLS — nessuna policy INSERT/UPDATE/DELETE
--     necessaria per il parser). Nessuna scrittura via anon.
--   • Tabelle dati di riferimento (fonti_online, requisiti_visti,
--     istruzioni_agente, dati_climatici): lettura pubblica, nessuna scrittura.
--   • conversazioni: lettura pubblica; da restringere a user_id quando si
--     implementa auth (aggiungere policy "auth.uid() = user_id").
--
-- IMPORTANTE: abilitare RLS senza policy blocca ogni accesso — ogni tabella
-- riceve almeno una policy SELECT prima di enable row level security.

-- ---------------------------------------------------------------------------
-- documenti — RAG index
-- ---------------------------------------------------------------------------
alter table documenti enable row level security;

-- Lettura pubblica: i documenti della KB sono acceduti dal frontend
-- per mostrare metadati (nome, categoria, n_chunks). Il service_role
-- bypassa comunque RLS per scrittura.
create policy "documenti_select_public"
  on documenti for select
  using (true);

-- ---------------------------------------------------------------------------
-- document_chunks — RLS già abilitata (migration Fase 0), aggiunge policy
-- ---------------------------------------------------------------------------
-- La Fase 0 ha abilitato RLS senza policy → nessun accesso anon funzionava.
-- La funzione match_chunks gira come SECURITY DEFINER implicito (sql stable),
-- ma viene invocata via PostgREST con anon key → serve policy SELECT.
create policy "document_chunks_select_public"
  on document_chunks for select
  using (true);

-- ---------------------------------------------------------------------------
-- fonti_online — dati di riferimento (fonti web curate)
-- ---------------------------------------------------------------------------
alter table fonti_online enable row level security;

create policy "fonti_online_select_public"
  on fonti_online for select
  using (true);

-- ---------------------------------------------------------------------------
-- requisiti_visti — dati di riferimento (visti per destinazione)
-- ---------------------------------------------------------------------------
alter table requisiti_visti enable row level security;

create policy "requisiti_visti_select_public"
  on requisiti_visti for select
  using (true);

-- ---------------------------------------------------------------------------
-- istruzioni_agente — prompt/istruzioni di sistema (lettura dal backend)
-- ---------------------------------------------------------------------------
alter table istruzioni_agente enable row level security;

create policy "istruzioni_agente_select_public"
  on istruzioni_agente for select
  using (true);

-- ---------------------------------------------------------------------------
-- dati_climatici — dati di riferimento (clima per paese/mese)
-- ---------------------------------------------------------------------------
alter table dati_climatici enable row level security;

create policy "dati_climatici_select_public"
  on dati_climatici for select
  using (true);

-- ---------------------------------------------------------------------------
-- conversazioni — storico conversazioni utente
-- ---------------------------------------------------------------------------
-- Lettura permissiva ora (app non ha ancora auth); da restringere con:
--   using (auth.uid()::text = user_id)  (o campo equivalente)
-- quando si implementa autenticazione utente.
alter table conversazioni enable row level security;

create policy "conversazioni_select_public"
  on conversazioni for select
  using (true);
