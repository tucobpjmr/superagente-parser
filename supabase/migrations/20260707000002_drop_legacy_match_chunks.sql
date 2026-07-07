-- D7 (Fase C) — Rimuove la versione legacy di match_chunks.
--
-- Firma legacy (pre-Fase-0): match_chunks(vector, text, int, float8)
-- usava match_modulo + similarity_threshold, senza RRF né FTS.
-- Sostituita dalla versione multidisciplinare in 20260609000001.
-- Nessun codice nel repo né nell'app la chiama (grep confermato).
-- Rimozione necessaria per eliminare il WARN "Function Search Path Mutable"
-- sull'advisor di sicurezza Supabase.

drop function if exists match_chunks(
  vector, text, integer, double precision
);
