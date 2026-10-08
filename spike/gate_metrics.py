"""Phase 0b (D11) gate metrics: cross-engine disagreement as the review gate, measured at line level vs ground truth.

Projection: every engine's page text is word-aligned to the GT word sequence (Levenshtein backtrace); each GT line
collects the engine words aligned to its words (insertions attach to the preceding GT word's line). This gives every
engine, whatever its native segmentation, one reading per GT line.

Per engine pair (X primary, Y) and for the full set (all pairwise-agree, first engine primary), at each tau:
  correct(e, line)  := CER(gt_line, e_line) <= tau
  agree(X, Y, line) := edit(X_line, Y_line) / max(len) <= tau
  silent_error = P(X wrong | agree)            <- what passes the gate unreviewed
  gate_recall  = P(disagree | X or Y wrong)
  gate_cost    = P(disagree | X and Y correct)
Also: oracle CER (per-line min across engines) vs the best single engine; shared autocorrections (GT word not in
the dictionary, >= 2 engines emit the same dictionary word aligned to it).

GT must be OWNER_VERIFIED (else --allow-draft, output stamped NOT_REPORTABLE).
Usage: python3 spike/gate_metrics.py --gt data/transcriptions/sheet_001 --engine NAME=FMT:DIR ... --out f.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score import READERS, WORDLIST, levenshtein_ops, norm  # noqa: E402

TAUS = [0.0, 0.05, 0.10]


def align_words(gt: list[str], hyp: list[str]) -> list[list[str]]:
    """For each GT word index, the hyp words aligned to it (substitution/match -> 1 word; insertions appended)."""
    n, m = len(gt), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + (gt[i - 1] != hyp[j - 1]))
    out: list[list[str]] = [[] for _ in range(n)]
    i, j, pending = n, m, []
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + (gt[i - 1] != hyp[j - 1]):
            out[i - 1] = [hyp[j - 1]] + pending + out[i - 1]
            pending, i, j = [], i - 1, j - 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            out[i - 1] = pending + out[i - 1]
            pending, i = [], i - 1
        else:
            pending, j = [hyp[j - 1]] + pending, j - 1
    if pending and n:
        out[0] = pending + out[0]
    return out


def project(gt_lines: list[str], hyp_text: str) -> tuple[list[str], list[list[str]]]:
    words, owner = [], []
    for li, ln in enumerate(gt_lines):
        for w in ln.split():
            words.append(w)
            owner.append(li)
    per_word = align_words(words, norm(hyp_text, strip_markup=True).split())
    lines = [[] for _ in gt_lines]
    for wi, ws in enumerate(per_word):
        lines[owner[wi]].extend(ws)
    return [" ".join(x) for x in lines], per_word


def ned(a: str, b: str) -> float:
    if not a and not b:
        return 0.0
    return levenshtein_ops(a, b)[0] / max(len(a), len(b))


def cer(gt: str, hyp: str) -> float:
    return levenshtein_ops(gt, hyp)[0] / max(1, len(gt))


def pair_stats(names: list[str], gt_lines: list[str], proj: dict[str, list[str]], tau: float) -> dict:
    primary = names[0]
    agree_n = agree_wrong = anywrong_n = anywrong_dis = bothok_n = bothok_dis = 0
    for li, g in enumerate(gt_lines):
        reads = [proj[n][li] for n in names]
        agree = all(ned(a, b) <= tau for a, b in itertools.combinations(reads, 2))
        wrong = [cer(g, r) > tau for r in reads]
        if agree:
            agree_n += 1
            agree_wrong += wrong[0]
        if any(wrong):
            anywrong_n += 1
            anywrong_dis += not agree
        else:
            bothok_n += 1
            bothok_dis += not agree
    f = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"engines": names, "primary": primary, "tau": tau, "lines": len(gt_lines),
            "agree_lines": agree_n, "silent_error_rate": f(agree_wrong, agree_n), "silent_errors": agree_wrong,
            "gate_recall": f(anywrong_dis, anywrong_n), "lines_any_wrong": anywrong_n,
            "gate_cost": f(bothok_dis, bothok_n), "lines_all_correct": bothok_n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gt", required=True, action="append", help="transcription dir (repeatable: pooled)")
    ap.add_argument("--engine", action="append", required=True,
                    help="NAME=FMT:DIR_TEMPLATE with {sheet} placeholder, e.g. B=lines:runs/X/raw/{sheet}/b_v5s")
    ap.add_argument("--out", required=True)
    ap.add_argument("--allow-draft", action="store_true")
    args = ap.parse_args()
    engines = {}
    for spec in args.engine:
        name, rest = spec.split("=", 1)
        fmt, d = rest.split(":", 1)
        engines[name] = (fmt, d)
    vocab = {w.strip().lower() for w in WORDLIST.read_text(errors="ignore").split()} if WORDLIST.exists() else set()

    statuses, gt_lines_all, proj_all, perword_all, gtwords_all = [], [], {n: [] for n in engines}, {n: [] for n in engines}, []
    for gdir in map(Path, args.gt):
        man = json.loads((gdir / "manifest.json").read_text())
        statuses.append(man["status"])
        sheet = gdir.name
        for pf in man["pages"]:
            gt_lines = [norm(l) for l in (gdir / pf).read_text().splitlines() if norm(l)]
            if not gt_lines:
                continue
            gt_lines_all += gt_lines
            gtwords_all += [w for l in gt_lines for w in l.split()]
            for n, (fmt, tmpl) in engines.items():
                f = Path(tmpl.format(sheet=sheet)) / f"{Path(pf).stem}.json"
                hyp = "\n".join(READERS[fmt](json.loads(f.read_text()))) if f.exists() else ""
                lines, per_word = project(gt_lines, hyp)
                proj_all[n] += lines
                perword_all[n] += per_word
    verified = all(s == "OWNER_VERIFIED" for s in statuses)
    if not verified and not args.allow_draft:
        print(f"refusing: GT status {statuses} (need OWNER_VERIFIED)", file=sys.stderr)
        return 2

    names = list(engines)
    combos = [list(c) for c in itertools.permutations(names, 2)] + ([names] if len(names) > 2 else [])
    gates = [pair_stats(c, gt_lines_all, proj_all, t) for t in TAUS for c in combos]
    tot = sum(len(g) for g in gt_lines_all)
    single = {n: round(sum(levenshtein_ops(g, proj_all[n][i])[0] for i, g in enumerate(gt_lines_all)) / tot, 4) for n in names}
    oracle = round(sum(min(levenshtein_ops(g, proj_all[n][i])[0] for n in names) for i, g in enumerate(gt_lines_all)) / tot, 4)
    shared = []
    for wi, gw in enumerate(gtwords_all):
        gc = re.sub(r"\W", "", gw.lower())
        if not gc or gc in vocab:
            continue
        emitted = {}
        for n in names:
            ws = perword_all[n][wi]
            if len(ws) == 1:
                hc = re.sub(r"\W", "", ws[0].lower())
                if hc and hc != gc and hc in vocab:
                    emitted.setdefault(hc, []).append(n)
        shared += [{"written": gw, "emitted": w, "engines": e} for w, e in emitted.items() if len(e) >= 2]
    res = {"reportable": verified, "gt_status": statuses, "lines": len(gt_lines_all), "gt_chars": tot,
           "single_engine_cer": single, "oracle_cer": oracle, "best_single": min(single, key=single.get),
           "shared_autocorrections": shared, "gates": gates}
    if not verified:
        res["WARNING"] = "NOT_REPORTABLE: scored against unverified draft transcriptions"
    Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k not in ("gates", "shared_autocorrections")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
