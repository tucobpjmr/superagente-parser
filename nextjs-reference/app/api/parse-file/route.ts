import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";
import { randomUUID } from "crypto";

export const runtime = "nodejs";
export const maxDuration = 60;

const PYTHON_PARSER_URL = process.env.PYTHON_PARSER_URL!;
const PARSER_SHARED_SECRET = process.env.PARSER_SHARED_SECRET!;
const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY!;

const PARSER_TIMEOUT_MS = 60_000;

type ParserChunk = {
  chunk_index: number;
  contenuto: string;
  embedding: number[];
  heading: string | null;
  modulo: string;
  categoria: string;
  documento_id: string | null;
};

type ParserResponse = {
  markdown: string;
  chunks: ParserChunk[];
  metadata: {
    file: string;
    size_mb: number;
    modulo: string;
    categoria: string;
    n_chunks: number;
    embedding_model: string;
    embedding_dim: number;
  };
};

function supabaseAdmin() {
  return createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

export async function POST(req: NextRequest) {
  if (!PYTHON_PARSER_URL || !PARSER_SHARED_SECRET) {
    return NextResponse.json(
      { error: "Parser non configurato (PYTHON_PARSER_URL / PARSER_SHARED_SECRET mancanti)" },
      { status: 500 },
    );
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return NextResponse.json({ error: "Body non valido: atteso multipart/form-data" }, { status: 400 });
  }

  const file = form.get("file");
  const modulo = form.get("modulo");
  const categoria = form.get("categoria");
  const documentoIdRaw = form.get("documento_id");

  if (!(file instanceof File)) {
    return NextResponse.json({ error: "Campo 'file' mancante" }, { status: 400 });
  }
  if (typeof modulo !== "string" || !modulo) {
    return NextResponse.json({ error: "Campo 'modulo' mancante" }, { status: 400 });
  }
  if (typeof categoria !== "string" || !categoria) {
    return NextResponse.json({ error: "Campo 'categoria' mancante" }, { status: 400 });
  }

  const documento_id =
    typeof documentoIdRaw === "string" && documentoIdRaw.length > 0
      ? documentoIdRaw
      : randomUUID();

  // --- 1. Forward multipart al microservizio Python ---
  const upstreamForm = new FormData();
  upstreamForm.append("file", file, file.name);
  upstreamForm.append("modulo", modulo);
  upstreamForm.append("categoria", categoria);
  upstreamForm.append("documento_id", documento_id);

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), PARSER_TIMEOUT_MS);

  let parserResp: Response;
  try {
    parserResp = await fetch(`${PYTHON_PARSER_URL.replace(/\/$/, "")}/parse`, {
      method: "POST",
      headers: { Authorization: `Bearer ${PARSER_SHARED_SECRET}` },
      body: upstreamForm,
      signal: ctrl.signal,
    });
  } catch (err: unknown) {
    const aborted = err instanceof Error && err.name === "AbortError";
    return NextResponse.json(
      {
        error: aborted
          ? `Timeout parser (>${PARSER_TIMEOUT_MS / 1000}s)`
          : `Errore di rete verso il parser: ${(err as Error).message}`,
      },
      { status: aborted ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }

  if (!parserResp.ok) {
    const detail = await parserResp.text().catch(() => "");
    return NextResponse.json(
      { error: `Parser HTTP ${parserResp.status}`, detail },
      { status: parserResp.status === 401 ? 500 : 502 },
    );
  }

  let parsed: ParserResponse;
  try {
    parsed = (await parserResp.json()) as ParserResponse;
  } catch {
    return NextResponse.json({ error: "Risposta parser non JSON" }, { status: 502 });
  }

  if (!Array.isArray(parsed.chunks) || parsed.chunks.length === 0) {
    return NextResponse.json({ error: "Parser non ha restituito chunk" }, { status: 422 });
  }

  // --- 2. Bulk INSERT in document_chunks ---
  const rows = parsed.chunks.map((c) => ({
    documento_id,
    chunk_index: c.chunk_index,
    contenuto: c.contenuto,
    embedding: c.embedding,
    modulo,
    categoria,
  }));

  const sb = supabaseAdmin();
  const { error: insertError } = await sb.from("document_chunks").insert(rows);

  if (insertError) {
    return NextResponse.json(
      { error: "Errore INSERT Supabase", detail: insertError.message },
      { status: 500 },
    );
  }

  return NextResponse.json({
    ok: true,
    documento_id,
    n_chunks: parsed.chunks.length,
    metadata: parsed.metadata,
  });
}
