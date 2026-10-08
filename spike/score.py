"""Phase 0 spike scorer: CER/WER and critical-token accuracy per engine vs. ground truth.

Stdlib only. Ground truth must be OWNER_VERIFIED (docs/TRANSCRIPTION_GUIDE.md); with
--allow-draft the output is stamped NOT_REPORTABLE and must never be quoted as a metric.

Usage:
  python3 spike/score.py --gt data/transcriptions/sheet_001 \
      --a spike/runs/<run_a>/sheet_001/engine_a --b spike/runs/<run_b>/sheet_001/engine_b \
      --out spike/runs/<run_a>/sheet_001/scores.json [--allow-draft]
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

WORDLIST = Path("/usr/share/dict/american-english")
CRITICAL_RE = re.compile(r"\d+(?:\.\d+)?|[%+=×÷]|(?<![A-Za-z])[a-dA-D](?=[\)\]])")
# Engine-introduced markup (stripped from engine output only).
MD_RE = re.compile(r"(\*\*|__|^#+\s*|</?[a-z][^>]*>)", re.MULTILINE)
# Line-leading bullet markers: stripped from BOTH sides, since a student's "*" bullet may come
# back as markdown "- " or "* ". Bullets are therefore not measured by CER.
BULLET_RE = re.compile(r"^\s*[-*\u2022]\s+(?=\S)", re.MULTILINE)


def levenshtein(a: list | str, b: list | str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def norm(text: str, strip_markup: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text)
    if strip_markup:
        text = MD_RE.sub("", text)
    text = BULLET_RE.sub("", text)
    text = text.replace("[?]", "").replace("→", "->").replace("⇒", "=>")
    return re.sub(r"\s+", " ", text).strip()


def engine_a_text(rec: dict) -> str:
    if rec["regions"]:
        return "\n".join(r["text"] for r in rec["regions"])
    return re.sub(r"<\|[^|]*\|>", " ", rec["raw"])


def engine_b_text(rec: dict) -> str:
    # Reading order: sort lines top-to-bottom, then left-to-right within ~half a line height.
    lines = sorted(rec["lines"], key=lambda l: (l["box"][1], l["box"][0]))
    rows: list[list[dict]] = []
    for ln in lines:
        h = max(1, ln["box"][3] - ln["box"][1])
        if rows and abs(ln["box"][1] - rows[-1][0]["box"][1]) < h * 0.5:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return "\n".join(" ".join(l["text"] for l in sorted(r, key=lambda l: l["box"][0])) for r in rows)


def critical_tokens(text: str) -> Counter:
    return Counter(CRITICAL_RE.findall(text))


def autocorrect_candidates(gt_words: list[str], hyp_words: list[str], vocab: set[str]) -> list[dict]:
    """GT word NOT in dictionary, engine emitted a dictionary word at the aligned position."""
    out = []
    sm = difflib.SequenceMatcher(a=[w.lower() for w in gt_words], b=[w.lower() for w in hyp_words], autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace" and (i2 - i1) == (j2 - j1):
            for g, h in zip(gt_words[i1:i2], hyp_words[j1:j2]):
                gc, hc = re.sub(r"\W", "", g.lower()), re.sub(r"\W", "", h.lower())
                if gc and hc and gc != hc and gc not in vocab and hc in vocab:
                    out.append({"written": g, "engine": h})
    return out


def score_pair(gt: str, hyp: str, vocab: set[str]) -> dict:
    g, h = norm(gt), norm(hyp, strip_markup=True)
    gw, hw = g.split(), h.split()
    cg, ch = critical_tokens(gt.replace("[?]", "")), critical_tokens(hyp)
    matched = sum((cg & ch).values())
    return {
        "gt_chars": len(g), "cer": round(levenshtein(g, h) / max(1, len(g)), 4),
        "cer_caseless": round(levenshtein(g.lower(), h.lower()) / max(1, len(g)), 4),
        "gt_words": len(gw), "wer": round(levenshtein(gw, hw) / max(1, len(gw)), 4),
        "critical": {"gt": sum(cg.values()), "matched": matched,
                     "recall": round(matched / sum(cg.values()), 4) if cg else None,
                     "missing": sorted((cg - ch).elements()), "extra": sorted((ch - cg).elements())},
        "autocorrect_candidates": autocorrect_candidates(gw, hw, vocab),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--a", help="dir of Engine A page JSONs")
    ap.add_argument("--b", help="dir of Engine B page JSONs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()

    gt_dir = Path(args.gt)
    engine_dirs = {"engine_a": Path(args.a) if args.a else None, "engine_b": Path(args.b) if args.b else None}
    manifest = json.loads((gt_dir / "manifest.json").read_text())
    verified = manifest["status"] == "OWNER_VERIFIED"
    if not verified and not args.allow_draft:
        print(f"refusing: ground truth status is {manifest['status']} (need OWNER_VERIFIED)", file=sys.stderr)
        return 2
    vocab = {w.strip().lower() for w in WORDLIST.read_text(errors="ignore").split()} if WORDLIST.exists() else set()

    readers = {"engine_a": engine_a_text, "engine_b": engine_b_text}
    results: dict = {"reportable": verified, "gt_status": manifest["status"], "pages": {},
                     "inputs": {k: str(v) for k, v in engine_dirs.items()}}
    if not verified:
        results["WARNING"] = "NOT_REPORTABLE: scored against unverified draft transcriptions"
    for page_file in manifest["pages"]:
        stem = Path(page_file).stem
        gt = (gt_dir / page_file).read_text()
        results["pages"][stem] = {}
        for eng, reader in readers.items():
            if engine_dirs[eng] is None:
                continue
            f = engine_dirs[eng] / f"{stem}.json"
            if f.exists():
                results["pages"][stem][eng] = score_pair(gt, reader(json.loads(f.read_text())), vocab)

    totals = {}
    for eng in readers:
        pp = [p[eng] for p in results["pages"].values() if eng in p]
        if not pp:
            continue
        chars, words = sum(p["gt_chars"] for p in pp), sum(p["gt_words"] for p in pp)
        crit_gt, crit_m = sum(p["critical"]["gt"] for p in pp), sum(p["critical"]["matched"] for p in pp)
        totals[eng] = {
            "pages": len(pp),
            "cer_micro": round(sum(p["cer"] * p["gt_chars"] for p in pp) / chars, 4),
            "cer_caseless_micro": round(sum(p["cer_caseless"] * p["gt_chars"] for p in pp) / chars, 4),
            "wer_micro": round(sum(p["wer"] * p["gt_words"] for p in pp) / words, 4),
            "critical_recall": round(crit_m / crit_gt, 4) if crit_gt else None,
            "critical_gt_count": crit_gt,
            "autocorrect_candidates": sum(len(p["autocorrect_candidates"]) for p in pp),
        }
    results["totals"] = totals
    Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False))
    tag = "" if verified else "  [NOT_REPORTABLE: draft ground truth]"
    print(f"scores -> {args.out}{tag}")
    for eng, t in totals.items():
        print(f"{eng}: {json.dumps(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
