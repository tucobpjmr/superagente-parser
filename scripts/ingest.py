#!/usr/bin/env python3
"""
Ingestione standalone — popola la knowledge base Supabase (anello A).

`POST /parse` restituisce i chunk con embedding ma NON scrive su Supabase: lo
deve fare il caller. In produzione il caller è la route Next.js; questo script
è l'alternativa "da riga di comando" per popolare la KB senza dipendere dal
frontend (test live, eval, backfill).

Flusso per ogni file:

  file locale
     │  multipart POST /parse  (Authorization: Bearer <secret>)
     ▼
  { markdown, chunks[], metadata }
     │  INSERT documenti        (PostgREST + service_role key)
     │    nome_file, tipo_file, categoria, modulo, contenuto_testo,
     │    riassunto, content_hash, n_chunks, dimensione_bytes, discipline[]
     ▼
  documento_id
     │  INSERT document_chunks[] (a batch)
     ▼
  KB popolata → /search e /answer restituiscono risultati

Idempotenza: prima di chiamare /parse lo script calcola lo SHA-256 del file e
salta i documenti il cui `content_hash` è già presente (stesso hash della cache
D4). `--force` cancella e re-ingerisce. Su errore durante l'inserimento dei
chunk il documento appena creato viene rimosso (niente documenti orfani).

Esempio:

  python scripts/ingest.py \\
    --parser-url https://<railway>.up.railway.app \\
    --secret $PARSER_SHARED_SECRET \\
    --supabase-url https://pxtwdhhulobyrheioiex.supabase.co \\
    --supabase-key $SUPABASE_SERVICE_KEY \\
    --modulo contrattualistica --categoria normativa \\
    --file documento.pdf --file altro.docx

  # oppure un'intera cartella:
  python scripts/ingest.py ... --dir ./documenti --modulo turismo --categoria faq

Secret e chiavi si possono passare anche via env: PARSER_SHARED_SECRET,
SUPABASE_URL, SUPABASE_SERVICE_KEY.
"""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# Estensioni accettate dal parser (allineate a EXT_TO_MIME in main.py).
SUPPORTED_EXTS = {
    ".pdf", ".docx", ".xlsx", ".xls", ".pptx", ".ppt", ".html", ".htm",
    ".txt", ".md", ".csv", ".json", ".png", ".jpg", ".jpeg", ".webp",
}

CHUNK_INSERT_BATCH = 100


@dataclass
class Config:
    parser_url: str
    secret: Optional[str]
    supabase_url: str
    supabase_key: str
    parse_timeout: float = 120.0
    http_timeout: float = 30.0


class IngestError(RuntimeError):
    """Errore non recuperabile durante l'ingestione di un singolo file."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vector_literal(embedding: List[float]) -> str:
    """
    Serializza l'embedding nel formato testuale di pgvector: '[0.1,-0.2,...]'.
    Inviato come stringa JSON, Postgres lo casta a vector(1536) senza ambiguità
    (una JSON array nuda può fallire il cast lato PostgREST).
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


def _supabase_headers(cfg: Config, *, prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": cfg.supabase_key,
        "Authorization": f"Bearer {cfg.supabase_key}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def collect_files(files: List[str], directory: Optional[str]) -> List[Path]:
    """Raccoglie i path espliciti (--file) e quelli della cartella (--dir)."""
    out: List[Path] = []
    for f in files:
        p = Path(f)
        if not p.is_file():
            raise IngestError(f"file non trovato: {f}")
        out.append(p)
    if directory:
        d = Path(directory)
        if not d.is_dir():
            raise IngestError(f"cartella non trovata: {directory}")
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
                out.append(p)
    # Deduplica preservando l'ordine.
    seen = set()
    unique: List[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


# ---------------------------------------------------------------------------
# Passi dell'ingestione (funzioni piccole e testabili col mock httpx)
# ---------------------------------------------------------------------------

def parse_document(
    http: httpx.Client, cfg: Config, path: Path, modulo: str, categoria: str
) -> Dict:
    """Chiama POST /parse e restituisce il JSON (markdown + chunks + metadata)."""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = {}
    if cfg.secret:
        headers["Authorization"] = f"Bearer {cfg.secret}"
    with path.open("rb") as fh:
        resp = http.post(
            f"{cfg.parser_url.rstrip('/')}/parse",
            headers=headers,
            data={"modulo": modulo, "categoria": categoria},
            files={"file": (path.name, fh, mime)},
            timeout=cfg.parse_timeout,
        )
    if resp.status_code != 200:
        raise IngestError(f"/parse ha risposto {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def find_existing_document(http: httpx.Client, cfg: Config, content_hash: str) -> Optional[str]:
    """Restituisce l'id del documento con quel content_hash, o None."""
    resp = http.get(
        f"{cfg.supabase_url.rstrip('/')}/rest/v1/documenti",
        headers=_supabase_headers(cfg),
        params={"content_hash": f"eq.{content_hash}", "select": "id", "limit": "1"},
        timeout=cfg.http_timeout,
    )
    if resp.status_code >= 400:
        raise IngestError(f"lookup content_hash fallito ({resp.status_code}): {resp.text[:200]}")
    rows = resp.json()
    return rows[0]["id"] if rows else None


def delete_document(http: httpx.Client, cfg: Config, documento_id: str) -> None:
    """Cancella un documento (cascade → document_chunks)."""
    resp = http.delete(
        f"{cfg.supabase_url.rstrip('/')}/rest/v1/documenti",
        headers=_supabase_headers(cfg),
        params={"id": f"eq.{documento_id}"},
        timeout=cfg.http_timeout,
    )
    if resp.status_code >= 400:
        raise IngestError(f"delete documento fallito ({resp.status_code}): {resp.text[:200]}")


def insert_document(
    http: httpx.Client,
    cfg: Config,
    *,
    parsed: Dict,
    modulo: str,
    categoria: str,
    tipo_file: str,
    dimensione_bytes: int,
) -> str:
    """INSERT su `documenti`, restituisce l'id generato."""
    meta = parsed.get("metadata", {})
    chunks = parsed.get("chunks", [])

    # Unione ordinata delle discipline dei chunk → discipline del documento.
    discipline: List[str] = []
    for ch in chunks:
        for d in ch.get("discipline") or []:
            if d not in discipline:
                discipline.append(d)

    row = {
        "nome_file": meta.get("file") or "unnamed",
        "tipo_file": tipo_file,
        "categoria": categoria,
        "modulo": modulo,
        "contenuto_testo": parsed.get("markdown"),
        "riassunto": meta.get("riassunto_documento"),
        "content_hash": meta.get("content_hash"),
        "n_chunks": meta.get("n_chunks", len(chunks)),
        "dimensione_bytes": dimensione_bytes,
        "discipline": discipline,
    }
    resp = http.post(
        f"{cfg.supabase_url.rstrip('/')}/rest/v1/documenti",
        headers=_supabase_headers(cfg, prefer="return=representation"),
        json=row,
        timeout=cfg.http_timeout,
    )
    if resp.status_code >= 400:
        raise IngestError(f"insert documenti fallito ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if not data:
        raise IngestError("insert documenti: risposta vuota (atteso return=representation)")
    return data[0]["id"]


def insert_chunks(
    http: httpx.Client,
    cfg: Config,
    *,
    documento_id: str,
    chunks: List[Dict],
    modulo: str,
    categoria: str,
) -> int:
    """INSERT a batch su `document_chunks`. Restituisce il numero inserito."""
    rows = []
    for ch in chunks:
        rows.append({
            "documento_id": documento_id,
            "chunk_index": ch["chunk_index"],
            "contenuto": ch["contenuto"],
            "embedding": _vector_literal(ch["embedding"]),
            # heading_path (breadcrumb completo) è più ricco per l'FTS; fallback
            # all'heading immediato se il parser non lo espone.
            "heading": ch.get("heading_path") or ch.get("heading"),
            "modulo": modulo,
            "categoria": categoria,
            "discipline": ch.get("discipline") or [],
            "tags": ch.get("tags") or [],
            "entities": ch.get("entities"),
        })

    inserted = 0
    for start in range(0, len(rows), CHUNK_INSERT_BATCH):
        batch = rows[start:start + CHUNK_INSERT_BATCH]
        resp = http.post(
            f"{cfg.supabase_url.rstrip('/')}/rest/v1/document_chunks",
            headers=_supabase_headers(cfg, prefer="return=minimal"),
            json=batch,
            timeout=cfg.http_timeout,
        )
        if resp.status_code >= 400:
            raise IngestError(f"insert chunks fallito ({resp.status_code}): {resp.text[:300]}")
        inserted += len(batch)
    return inserted


def ingest_file(
    http: httpx.Client,
    cfg: Config,
    path: Path,
    modulo: str,
    categoria: str,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> Dict:
    """
    Ingerisce un singolo file. Ritorna un dict di esito:
      {status: 'ingested'|'skipped'|'dry-run', documento_id, n_chunks, file}
    Solleva IngestError su fallimento non recuperabile.
    """
    content_hash = sha256_file(path)
    dimensione_bytes = path.stat().st_size
    tipo_file = path.suffix.lower().lstrip(".") or "bin"

    # Idempotenza: evita di ri-embeddare un documento già presente.
    existing = find_existing_document(http, cfg, content_hash)
    if existing and not force:
        return {"status": "skipped", "documento_id": existing, "n_chunks": 0, "file": path.name}

    parsed = parse_document(http, cfg, path, modulo, categoria)
    chunks = parsed.get("chunks", [])
    if not chunks:
        raise IngestError("/parse non ha restituito chunk")

    if dry_run:
        return {
            "status": "dry-run",
            "documento_id": None,
            "n_chunks": len(chunks),
            "file": path.name,
        }

    if existing and force:
        delete_document(http, cfg, existing)

    documento_id = insert_document(
        http, cfg,
        parsed=parsed, modulo=modulo, categoria=categoria,
        tipo_file=tipo_file, dimensione_bytes=dimensione_bytes,
    )
    try:
        n = insert_chunks(
            http, cfg,
            documento_id=documento_id, chunks=chunks,
            modulo=modulo, categoria=categoria,
        )
    except IngestError:
        # Niente documenti orfani: rimuovi il documento appena creato.
        try:
            delete_document(http, cfg, documento_id)
        except IngestError:
            pass
        raise
    return {"status": "ingested", "documento_id": documento_id, "n_chunks": n, "file": path.name}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_config(args: argparse.Namespace) -> Config:
    secret = args.secret or os.getenv("PARSER_SHARED_SECRET")
    supabase_url = args.supabase_url or os.getenv("SUPABASE_URL")
    supabase_key = args.supabase_key or os.getenv("SUPABASE_SERVICE_KEY")
    missing = []
    if not supabase_url:
        missing.append("--supabase-url / SUPABASE_URL")
    if not supabase_key:
        missing.append("--supabase-key / SUPABASE_SERVICE_KEY")
    if missing:
        raise SystemExit("Configurazione mancante: " + ", ".join(missing))
    return Config(
        parser_url=args.parser_url,
        secret=secret,
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        parse_timeout=args.parse_timeout,
        http_timeout=args.http_timeout,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ingerisce documenti nella KB Supabase via il parser /parse.",
    )
    p.add_argument("--parser-url", required=True, help="Base URL del parser (es. https://x.up.railway.app)")
    p.add_argument("--secret", help="PARSER_SHARED_SECRET (o env PARSER_SHARED_SECRET)")
    p.add_argument("--supabase-url", help="URL progetto Supabase (o env SUPABASE_URL)")
    p.add_argument("--supabase-key", help="service_role key (o env SUPABASE_SERVICE_KEY)")
    p.add_argument("--modulo", required=True, help="Disciplina principale / hint legacy")
    p.add_argument("--categoria", required=True, help="Categoria libera")
    p.add_argument("--file", action="append", default=[], help="File da ingerire (ripetibile)")
    p.add_argument("--dir", dest="directory", help="Cartella: ingerisce tutti i file supportati")
    p.add_argument("--force", action="store_true", help="Re-ingerisci anche se content_hash già presente")
    p.add_argument("--dry-run", action="store_true", help="Chiama /parse ma non scrive su Supabase")
    p.add_argument("--parse-timeout", type=float, default=120.0, help="Timeout /parse in secondi (default 120)")
    p.add_argument("--http-timeout", type=float, default=30.0, help="Timeout chiamate Supabase (default 30)")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    cfg = build_config(args)

    try:
        paths = collect_files(args.file, args.directory)
    except IngestError as e:
        print(f"errore: {e}", file=sys.stderr)
        return 2
    if not paths:
        print("nessun file da ingerire (usa --file o --dir)", file=sys.stderr)
        return 2

    counts = {"ingested": 0, "skipped": 0, "dry-run": 0, "failed": 0}
    with httpx.Client() as http:
        for path in paths:
            try:
                res = ingest_file(
                    http, cfg, path, args.modulo, args.categoria,
                    force=args.force, dry_run=args.dry_run,
                )
                counts[res["status"]] += 1
                tag = res["status"].upper()
                did = res["documento_id"] or "-"
                print(f"[{tag}] {res['file']}  documento_id={did}  chunk={res['n_chunks']}")
            except IngestError as e:
                counts["failed"] += 1
                print(f"[FAILED] {path.name}: {e}", file=sys.stderr)

    print(
        f"\nRiepilogo: {counts['ingested']} ingeriti, {counts['skipped']} saltati, "
        f"{counts['dry-run']} dry-run, {counts['failed']} falliti."
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
