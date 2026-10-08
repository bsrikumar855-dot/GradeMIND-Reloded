"""Per-page spike summary: Engine A degenerate flags (recomputed with the CURRENT detector), latency,
and a cross-engine coverage signal (Engine A text length vs Engine B confident-line text length).

Coverage is a heuristic omission signal, not an accuracy metric: Engine B also reads bleed-through, and
only lines with score >= --b-min-score are counted. Run with the engine_a venv (imports the detector).

Usage: spike/engine_a/.venv/bin/python spike/summarize.py --a <run_dir> --b <run_dir> [--out summary.json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "engine_a"))
from run_engine_a import degenerate_checks  # noqa: E402


def a_text(rec: dict) -> str:
    body = "\n".join(r["text"] for r in rec["regions"]) if rec["regions"] else rec["raw"]
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--b-min-score", type=float, default=0.7)
    ap.add_argument("--out")
    args = ap.parse_args()
    a_root, b_root = Path(args.a), Path(args.b)

    rows = []
    for a_file in sorted(a_root.glob("sheet_*/engine_a/page_*.json")):
        sheet, page = a_file.parts[-3], a_file.stem
        a = json.loads(a_file.read_text())
        b_file = b_root / sheet / "engine_b" / f"{page}.json"
        b = json.loads(b_file.read_text()) if b_file.exists() else None
        at = a_text(a)
        bt = " ".join(l["text"] for l in b["lines"] if l["score"] >= args.b_min_score) if b else ""
        flags = degenerate_checks(a["raw"], a["generated_tokens"], a["regions"],
                                  a.get("decoding", {}).get("max_length", 32768))
        rows.append({
            "sheet": sheet, "page": page, "a_latency_s": a["latency_s"], "a_tokens": a["generated_tokens"],
            "a_peak_mib": a["peak_torch_alloc_mib"], "a_flags": flags, "a_chars": len(at),
            "b_conf_chars": len(bt), "b_latency_s": b["latency_s"] if b else None,
            "coverage_a_over_b": round(len(at) / len(bt), 2) if bt else None,
        })

    print(f"{'sheet':10} {'page':8} {'A s':>6} {'A tok':>6} {'A chr':>6} {'B chr':>6} {'A/B':>5}  flags")
    for r in rows:
        print(f"{r['sheet']:10} {r['page']:8} {r['a_latency_s']:6.1f} {r['a_tokens']:6d} {r['a_chars']:6d} "
              f"{r['b_conf_chars']:6d} {str(r['coverage_a_over_b']):>5}  {','.join(r['a_flags'])}")
    answer = [r for r in rows if r["page"] != "page_01"]
    flagged = [r for r in answer if r["a_flags"]]
    low_cov = [r for r in answer if not r["a_flags"] and r["coverage_a_over_b"] is not None
               and r["coverage_a_over_b"] < 0.6]
    print(f"\nanswer pages: {len(answer)} | A degenerate-flagged: {len(flagged)} | "
          f"unflagged with A/B coverage < 0.6: {len(low_cov)}")
    if args.out:
        Path(args.out).write_text(json.dumps({"a_run": str(a_root), "b_run": str(b_root),
                                              "b_min_score": args.b_min_score, "rows": rows}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
