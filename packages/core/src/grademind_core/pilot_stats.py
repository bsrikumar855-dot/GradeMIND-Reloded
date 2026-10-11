"""Pilot measurement (5.6, D30.2): how wrong is the machine reading on the lines an examiner corrected?

Reads the corrections table (read only) and reports the character error rate (CER) of the machine's text against the
examiner's final text, with a 95% confidence interval and the number of lines it rests on. It prints numbers only: never a
line of student text, a crop, a student reference or a booklet id.

What the number is, and is not (it is printed with every report):
- The table holds only lines an examiner CHANGED. A line the machine read right and the examiner left alone leaves no row.
  So this is the error rate *among corrected lines*, and it overstates the machine's error rate over all lines. It is the
  measured size of the mistakes the examiners chose to fix, not an accuracy claim for the system.
- Lines the machine missed completely (the examiner added them) are counted separately; they have no machine text to
  compare.
- A line the examiner marked "[?]" (could not read it either) is left out and counted.
- Below MIN_N lines the script refuses: a rate from a handful of lines says nothing. D30.2 asks for the first 200
  corrections; between MIN_N and that it reports "interim".

Nothing here touches marks, verdicts or snapshots (D19, D28): it only reads `line_corrections`.
"""

from __future__ import annotations

import random
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.ocr_models import LineCorrection

MIN_N = 50  # fewer lines than this: no figure is reported
TARGET_N = 200  # D30.2: the first 200 real corrections give the measured error rate
BOOTSTRAP = 2000
SEED = 20261011  # fixed, so the same data always gives the same interval
UNREADABLE = "[?]"

CAVEAT = (
    "Read this number carefully: it is the error rate AMONG THE LINES AN EXAMINER CORRECTED. Lines the machine read right\n"
    "and the examiner left alone are not in the table, so the machine's error rate over all lines is LOWER than this. It is\n"
    "not an accuracy claim for the system, and it says nothing about any handwriting that is not in these lines."
)


class TooFewLinesError(ValueError):
    def __init__(self, n: int) -> None:
        super().__init__(f"only {n} corrected line(s): at least {MIN_N} are needed before any error rate is reported")
        self.n = n


def levenshtein(a: str, b: str) -> int:
    """Edit distance in code points (insert, delete, replace each cost 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class Pair:
    machine: str
    final: str


@dataclass(frozen=True)
class CerReport:
    n_lines: int
    n_chars: int  # characters in the examiners' final text (the denominator)
    edits: int
    cer: float  # total edits / total final characters
    cer_low: float  # 95% interval (percentile bootstrap over lines)
    cer_high: float
    mean_line_cer: float  # the average of each line's own rate (long and short lines count alike)
    exact_after_edit_le_1: float  # share of lines that were off by at most one character
    missed_lines: int  # lines the examiner had to add (no machine text)
    unreadable_left_out: int
    interim: bool  # fewer than TARGET_N lines


def _norm(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip()


def cer_report(pairs: list[Pair], missed_lines: int = 0, unreadable_left_out: int = 0, boot: int = BOOTSTRAP) -> CerReport:
    """The report for these (machine text, final text) pairs. Raises TooFewLinesError below MIN_N lines."""
    usable = [(_norm(p.machine), _norm(p.final)) for p in pairs]
    usable = [(m, f) for m, f in usable if f]  # a line with no final text has no reference to measure against
    n = len(usable)
    if n < MIN_N:
        raise TooFewLinesError(n)
    dist = [levenshtein(m, f) for m, f in usable]
    ref = [len(f) for _, f in usable]
    total_edits, total_chars = sum(dist), sum(ref)
    rng = random.Random(SEED)  # noqa: S311 - a reproducible resample, not a secret
    rates: list[float] = []
    for _ in range(boot):
        idx = [rng.randrange(n) for _ in range(n)]
        rates.append(sum(dist[i] for i in idx) / sum(ref[i] for i in idx))
    rates.sort()
    low, high = rates[int(0.025 * boot)], rates[min(boot - 1, int(0.975 * boot))]
    return CerReport(
        n_lines=n,
        n_chars=total_chars,
        edits=total_edits,
        cer=total_edits / total_chars,
        cer_low=low,
        cer_high=high,
        mean_line_cer=sum(d / r for d, r in zip(dist, ref, strict=True)) / n,
        exact_after_edit_le_1=sum(1 for d in dist if d <= 1) / n,
        missed_lines=missed_lines,
        unreadable_left_out=unreadable_left_out,
        interim=n < TARGET_N,
    )


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def render(r: CerReport) -> str:
    """The text the command prints: numbers and the caveat, nothing from any line."""
    lines = [
        f"Machine reading, measured on {r.n_lines} corrected line(s) ({r.n_chars} characters of examiner text)"
        + (f"  [INTERIM: the protocol asks for {TARGET_N}]" if r.interim else ""),
        f"  Character error rate:      {pct(r.cer)}   (95% interval {pct(r.cer_low)} to {pct(r.cer_high)}; {r.edits} edits)",
        f"  Average per line:          {pct(r.mean_line_cer)}",
        f"  Off by one character or less: {pct(r.exact_after_edit_le_1)} of the corrected lines",
        f"  Lines the machine missed (added by an examiner): {r.missed_lines}  (no rate: there was nothing to compare)",
        f"  Left out because the examiner marked them unreadable ({UNREADABLE}): {r.unreadable_left_out}",
        "",
        CAVEAT,
    ]
    return "\n".join(lines)


def load_pairs(db: Session, exam_ids: list[uuid.UUID] | None = None, limit: int = TARGET_N) -> tuple[list[Pair], int, int]:
    """(pairs, lines the machine missed, lines left out as unreadable): the first `limit` corrected machine lines, oldest first.

    One pair per machine line: its text as the machine read it, and the examiner's current text for it (the end of the line's
    correction history). Corrections are read only; nothing is written."""
    superseded = select(LineCorrection.supersedes_id).where(LineCorrection.supersedes_id.is_not(None))
    q = select(LineCorrection).where(LineCorrection.id.not_in(superseded))  # the current correction of each line
    if exam_ids:
        q = q.where(LineCorrection.exam_id.in_(exam_ids))
    heads = list(db.scalars(q.order_by(LineCorrection.created_at, LineCorrection.id)))
    missed = sum(1 for h in heads if h.ocr_line_id is None)
    machine = [h for h in heads if h.ocr_line_id is not None]
    unreadable = [h for h in machine if UNREADABLE in h.corrected_text]
    usable = [h for h in machine if UNREADABLE not in h.corrected_text][:limit]
    return [Pair(h.ocr_text, h.corrected_text) for h in usable], missed, len(unreadable)
