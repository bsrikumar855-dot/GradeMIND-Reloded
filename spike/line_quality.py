"""Phase 0b (D9) line-detection quality vs ground truth, at ROW level (PaddleOCR splits one handwritten row into
several boxes at wide gaps by design, so box-level counts are meaningless).

Detected boxes -> rows (reading order, as in score.line_engine_lines). Each row's recognised text is matched to GT lines:
  matched  : best SequenceMatcher ratio to a single GT line >= MIN_SIM
  merged   : ratio to (GT line i + i+1) >= MIN_SIM and > best single-line ratio + MARGIN
  spurious : no GT line (or pair) reaches MIN_SIM  -> show-through / ruled-line noise / watermark text
  split    : a GT line that is the best match of >= 2 rows
  missed   : a GT line that no row matches
Caveat: matching uses RECOGNISED text, so heavy recognition errors can masquerade as detection errors.
GT must be OWNER_VERIFIED (else --allow-draft -> NOT_REPORTABLE).

Usage: python3 spike/line_quality.py --gt data/transcriptions/sheet_001 --engine NAME=DIR ... --out f.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score import line_engine_lines, norm  # noqa: E402

MIN_SIM, MARGIN = 0.5, 0.1


def sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower(), autojunk=False).ratio()


def classify(gt_lines: list[str], rows: list[str]) -> dict:
    owners: Counter = Counter()
    merged = spurious = 0
    for r in rows:
        r = norm(r, strip_markup=True)
        if not r:
            continue
        singles = [sim(r, g) for g in gt_lines]
        best_i = max(range(len(gt_lines)), key=singles.__getitem__)
        pairs = [sim(r, gt_lines[i] + " " + gt_lines[i + 1]) for i in range(len(gt_lines) - 1)]
        best_pair = max(pairs) if pairs else 0.0
        if best_pair >= MIN_SIM and best_pair > singles[best_i] + MARGIN:
            merged += 1
            j = pairs.index(best_pair)
            owners[j] += 1
            owners[j + 1] += 1
        elif singles[best_i] >= MIN_SIM:
            owners[best_i] += 1
        else:
            spurious += 1
    return {"gt_lines": len(gt_lines), "rows": len([r for r in rows if norm(r)]), "matched_gt_lines": len(owners),
            "missed": len(gt_lines) - len(owners), "split": sum(1 for c in owners.values() if c >= 2),
            "merged_rows": merged, "spurious_rows": spurious}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--engine", action="append", required=True, help="NAME=DIR (lines-format JSON)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()
    gdir = Path(args.gt)
    man = json.loads((gdir / "manifest.json").read_text())
    verified = man["status"] == "OWNER_VERIFIED"
    if not verified and not args.allow_draft:
        print(f"refusing: GT status {man['status']}", file=sys.stderr)
        return 2
    res = {"reportable": verified, "gt_status": man["status"], "pages": {}, "totals": {}}
    for spec in args.engine:
        name, d = spec.split("=", 1)
        tot: Counter = Counter()
        for pf in man["pages"]:
            gt_lines = [norm(l) for l in (gdir / pf).read_text().splitlines() if norm(l)]
            f = Path(d) / f"{Path(pf).stem}.json"
            if not f.exists():
                continue
            c = classify(gt_lines, line_engine_lines(json.loads(f.read_text())))
            res["pages"].setdefault(Path(pf).stem, {})[name] = c
            tot.update(c)
        res["totals"][name] = dict(tot)
    if not verified:
        res["WARNING"] = "NOT_REPORTABLE: scored against unverified draft transcriptions"
    Path(args.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res["totals"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
