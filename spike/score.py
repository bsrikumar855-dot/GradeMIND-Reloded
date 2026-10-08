"""OCR spike scorer (Phase 0 / 0b). Stdlib only.

Metrics per engine vs ground truth: CER, WER, word omissions/insertions/substitutions, autocorrection candidates,
question-label detection, MCQ option-letter accuracy, and spurious digits (digits the engine emits that are not
in the ground truth, e.g. read from show-through). Per owner decision D5, numeral/sign/unit accuracy is reported
as UNTESTED: the sample sheets contain almost no numerals.

Ground truth must be OWNER_VERIFIED (docs/TRANSCRIPTION_GUIDE.md). With --allow-draft the output is stamped
NOT_REPORTABLE and must never be quoted as a metric.

Usage:
  python3 spike/score.py --gt data/transcriptions/sheet_001 \\
      --engine A=a:spike/runs/<run>/sheet_001/engine_a --engine B=lines:spike/runs/<run>/sheet_001/engine_b \\
      --out <file.json> [--allow-draft]
  Formats: `a` = Engine A page JSON (regions/raw); `lines` = line JSON (Engine B, Engine C).
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
# Engine-introduced markup (stripped from engine output only).
MD_RE = re.compile(r"(\*\*|__|^#+\s*|</?[a-z][^>]*>)", re.MULTILINE)
# Line-leading bullet markers: stripped from BOTH sides (a student's "*" may come back as "- "), so bullets are not measured.
BULLET_RE = re.compile(r"^\s*[-*•]\s+(?=\S)", re.MULTILINE)
LABEL_RE = re.compile(r"^\s*(\d{1,2})(?=[\s.\]\):[]|$)")
OPTION_RE = re.compile(r"(?<![A-Za-z])([a-dA-D])\s*[\)\]]")
DIGIT_RE = re.compile(r"\d+")


def levenshtein_ops(a: list | str, b: list | str) -> tuple[int, int, int, int]:
    """Return (distance, substitutions, deletions, insertions) turning a (GT) into b (hyp)."""
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
    i, j, s, d, ins = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            s += a[i - 1] != b[j - 1]
            i, j = i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            d, i = d + 1, i - 1
        else:
            ins, j = ins + 1, j - 1
    return dp[n][m], s, d, ins


def norm(text: str, strip_markup: bool = False) -> str:
    text = unicodedata.normalize("NFKC", text)
    if strip_markup:
        text = MD_RE.sub(" ", text)
    text = BULLET_RE.sub("", text)
    text = text.replace("[?]", "").replace("→", "->").replace("⇒", "=>")
    return re.sub(r"\s+", " ", text).strip()


def engine_a_lines(rec: dict) -> list[str]:
    body = "\n".join(r["text"] for r in rec["regions"]) if rec["regions"] else re.sub(r"<\|[^|]*\|>", "\n", rec["raw"])
    body = re.sub(r"</t[rd]>|<br\s*/?>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    return [ln.strip() for ln in body.splitlines() if ln.strip()]


def line_engine_lines(rec: dict) -> list[str]:
    # Reading order: top-to-bottom; same row (within half a line height) left-to-right.
    lines = sorted(rec["lines"], key=lambda l: (l["box"][1], l["box"][0]))
    rows: list[list[dict]] = []
    for ln in lines:
        h = max(1, ln["box"][3] - ln["box"][1])
        if rows and abs(ln["box"][1] - rows[-1][0]["box"][1]) < h * 0.5:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [" ".join(l["text"] for l in sorted(r, key=lambda l: l["box"][0])) for r in rows]


READERS = {"a": engine_a_lines, "lines": line_engine_lines}


def labels(lines: list[str], skip_uncertain: bool) -> tuple[Counter, Counter]:
    """Question labels at line start. With skip_uncertain, labels marked `[?]` go to the second Counter."""
    found, uncertain = Counter(), Counter()
    for ln in lines:
        m = LABEL_RE.match(ln)
        if not m:
            continue
        if skip_uncertain and ln[m.end():m.end() + 3].startswith("[?]"):
            uncertain[int(m.group(1))] += 1
            continue
        found[int(m.group(1))] += 1
    return found, uncertain


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


def score_pair(gt_text: str, hyp_lines: list[str], vocab: set[str]) -> dict:
    hyp_text = "\n".join(hyp_lines)
    g, h = norm(gt_text), norm(hyp_text, strip_markup=True)
    gw, hw = g.split(), h.split()
    cdist, _, _, _ = levenshtein_ops(g, h)
    wdist, wsub, wdel, wins = levenshtein_ops(gw, hw)
    gl, gl_unc = labels(gt_text.splitlines(), skip_uncertain=True)
    hl, _ = labels(hyp_lines, skip_uncertain=False)
    hl = hl - (gl_unc - gl)  # a detection matching an excluded uncertain GT label is neither credited nor penalised
    lab_tp = sum((gl & hl).values())
    go, ho = Counter(m.lower() for m in OPTION_RE.findall(gt_text)), Counter(m.lower() for m in OPTION_RE.findall(hyp_text))
    gd, hd = Counter(DIGIT_RE.findall(gt_text.replace("[?]", ""))), Counter(DIGIT_RE.findall(hyp_text))
    return {
        "gt_chars": len(g), "cer": round(cdist / max(1, len(g)), 4),
        "gt_words": len(gw), "wer": round(wdist / max(1, len(gw)), 4),
        "word_ops": {"substitutions": wsub, "omissions": wdel, "insertions": wins},
        "autocorrect_candidates": autocorrect_candidates(gw, hw, vocab),
        "labels": {"gt": sum(gl.values()), "gt_uncertain_excluded": sum(gl_unc.values()), "detected": sum(hl.values()), "correct": lab_tp,
                   "missed": sorted((gl - hl).elements()), "spurious": sorted((hl - gl).elements())},
        "options": {"gt": sum(go.values()), "correct": sum((go & ho).values()), "missed": sorted((go - ho).elements())},
        "spurious_digit_tokens": sorted((hd - gd).elements()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True)
    ap.add_argument("--engine", action="append", required=True, help="NAME=FORMAT:DIR, FORMAT in {a, lines}")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()

    engines = {}
    for spec in args.engine:
        name, rest = spec.split("=", 1)
        fmt, d = rest.split(":", 1)
        if fmt not in READERS:
            ap.error(f"unknown format {fmt}")
        engines[name] = (fmt, Path(d))

    gt_dir = Path(args.gt)
    manifest = json.loads((gt_dir / "manifest.json").read_text())
    verified = manifest["status"] == "OWNER_VERIFIED"
    if not verified and not args.allow_draft:
        print(f"refusing: ground truth status is {manifest['status']} (need OWNER_VERIFIED)", file=sys.stderr)
        return 2
    vocab = {w.strip().lower() for w in WORDLIST.read_text(errors="ignore").split()} if WORDLIST.exists() else set()

    results: dict = {"reportable": verified, "gt_status": manifest["status"], "gt_dir": str(gt_dir),
                     "numeral_sign_unit_accuracy": "UNTESTED (owner decision D5: no numerical-subject sheet yet)",
                     "engines": {k: {"format": f, "dir": str(d)} for k, (f, d) in engines.items()}, "pages": {}}
    if not verified:
        results["WARNING"] = "NOT_REPORTABLE: scored against unverified draft transcriptions"
    for page_file in manifest["pages"]:
        stem = Path(page_file).stem
        gt = (gt_dir / page_file).read_text()
        results["pages"][stem] = {}
        for name, (fmt, d) in engines.items():
            f = d / f"{stem}.json"
            if f.exists():
                results["pages"][stem][name] = score_pair(gt, READERS[fmt](json.loads(f.read_text())), vocab)

    totals = {}
    for name in engines:
        pp = [p[name] for p in results["pages"].values() if name in p]
        if not pp:
            continue
        chars, words = sum(p["gt_chars"] for p in pp), sum(p["gt_words"] for p in pp)
        sums = lambda f: sum(f(p) for p in pp)  # noqa: E731
        lab_gt, lab_ok, lab_det = sums(lambda p: p["labels"]["gt"]), sums(lambda p: p["labels"]["correct"]), sums(lambda p: p["labels"]["detected"])
        totals[name] = {
            "pages": len(pp), "gt_chars": chars, "gt_words": words,
            "cer_micro": round(sums(lambda p: p["cer"] * p["gt_chars"]) / chars, 4),
            "wer_micro": round(sums(lambda p: p["wer"] * p["gt_words"]) / words, 4),
            "omissions": sums(lambda p: p["word_ops"]["omissions"]),
            "substitutions": sums(lambda p: p["word_ops"]["substitutions"]),
            "insertions": sums(lambda p: p["word_ops"]["insertions"]),
            "autocorrect_candidates": sums(lambda p: len(p["autocorrect_candidates"])),
            "label_recall": round(lab_ok / lab_gt, 4) if lab_gt else None,
            "label_precision": round(lab_ok / lab_det, 4) if lab_det else None,
            "labels_gt": lab_gt, "labels_correct": lab_ok, "labels_detected": lab_det,
            "option_letters_correct": f"{sums(lambda p: p['options']['correct'])}/{sums(lambda p: p['options']['gt'])}",
            "spurious_digit_tokens": sums(lambda p: len(p["spurious_digit_tokens"])),
        }
    results["totals"] = totals
    Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False))
    tag = "" if verified else "  [NOT_REPORTABLE: draft ground truth]"
    print(f"scores -> {args.out}{tag}")
    for name, t in totals.items():
        print(f"{name}: {json.dumps(t)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
