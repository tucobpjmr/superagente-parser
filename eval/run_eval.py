"""
Valutazione del retrieval (Fase 3.2): recall@k e MRR sul golden set.

Esegue ogni domanda di golden.jsonl contro POST /search e misura:
  - Recall@k : frazione dei riferimenti golden presenti nei top-k risultati
  - MRR      : reciproco del rank del primo risultato golden
  - Discipline coverage : frazione delle discipline attese coperte dai top-k

Un risultato "matcha" un golden item se:
  - il suo id è in `chunk_ids` (criterio esatto, da compilare dopo
    l'ingestion dei documenti reali), oppure
  - il suo contenuto contiene una delle `substrings` (criterio surrogato,
    case-insensitive, utile finché i chunk_ids non sono stati annotati).

Le metriche sono riportate separatamente per domande mono- e multi-disciplina.

Uso:
  python eval/run_eval.py --base-url https://<parser>.railway.app \
      --secret $PARSER_SHARED_SECRET [--top-k 8] [--golden eval/golden.jsonl]

Exit code 0 sempre (i numeri guidano il tuning, non sono un gate CI — per ora).
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx


def load_golden(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def is_golden_hit(result: dict, golden: dict) -> bool:
    if golden.get("chunk_ids") and result.get("id") in golden["chunk_ids"]:
        return True
    contenuto = (result.get("contenuto") or "").lower()
    return any(s.lower() in contenuto for s in golden.get("substrings", []))


def eval_one(client: httpx.Client, base_url: str, secret: str, golden: dict, top_k: int) -> dict:
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"

    t0 = time.monotonic()
    r = client.post(
        f"{base_url}/search",
        json={"domanda": golden["domanda"], "top_k": top_k},
        headers=headers,
        timeout=60,
    )
    duration = time.monotonic() - t0
    if r.status_code != 200:
        return {"id": golden["id"], "error": f"HTTP {r.status_code}: {r.text[:200]}", "duration_s": duration}

    risultati = r.json().get("risultati", [])[:top_k]

    hits = [i for i, res in enumerate(risultati) if is_golden_hit(res, golden)]
    n_refs = max(len(golden.get("chunk_ids") or []), 1)
    recall = min(len(hits) / n_refs, 1.0)
    mrr = 1.0 / (hits[0] + 1) if hits else 0.0

    attese = set(golden.get("discipline_attese", []))
    trovate = {d for res in risultati for d in (res.get("discipline") or [])}
    disc_cov = len(attese & trovate) / len(attese) if attese else 1.0

    return {
        "id": golden["id"],
        "multi": golden.get("multi", False),
        "recall": recall,
        "mrr": mrr,
        "disc_cov": disc_cov,
        "n_risultati": len(risultati),
        "duration_s": round(duration, 2),
    }


def summarize(rows: list[dict], label: str) -> None:
    rows = [r for r in rows if "error" not in r]
    if not rows:
        print(f"  {label}: nessun dato")
        return
    print(
        f"  {label:<18} n={len(rows):<3} "
        f"recall@k={statistics.mean(r['recall'] for r in rows):.3f}  "
        f"MRR={statistics.mean(r['mrr'] for r in rows):.3f}  "
        f"disc_cov={statistics.mean(r['disc_cov'] for r in rows):.3f}  "
        f"t_med={statistics.median(r['duration_s'] for r in rows):.2f}s"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="URL base del parser (senza /search)")
    ap.add_argument("--secret", default="", help="PARSER_SHARED_SECRET (se impostato)")
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--golden", type=Path, default=Path(__file__).parent / "golden.jsonl")
    ap.add_argument("--json-out", type=Path, help="Salva i risultati riga-per-riga in JSONL")
    args = ap.parse_args()

    golden_items = load_golden(args.golden)
    print(f"Golden set: {len(golden_items)} domande · top_k={args.top_k} · {args.base_url}\n")

    rows = []
    with httpx.Client() as client:
        for g in golden_items:
            row = eval_one(client, args.base_url.rstrip("/"), args.secret, g, args.top_k)
            rows.append(row)
            status = f"ERR {row['error']}" if "error" in row else (
                f"recall={row['recall']:.2f} mrr={row['mrr']:.2f} disc={row['disc_cov']:.2f}"
            )
            print(f"  [{g['id']}] {g['domanda'][:60]:<60} {status}")

    print("\n— Riepilogo —")
    summarize(rows, "tutte")
    summarize([r for r in rows if r.get("multi")], "multi-disciplina")
    summarize([r for r in rows if not r.get("multi")], "mono-disciplina")

    errors = [r for r in rows if "error" in r]
    if errors:
        print(f"\n⚠ {len(errors)} domande in errore")

    if args.json_out:
        args.json_out.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )
        print(f"\nRisultati salvati in {args.json_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
