"""Interval statistics for Phase 0b reports (owner decision D16). Stdlib only, deterministic.

wilson(k, n)              95% Wilson score interval for a rate.
bootstrap_ratio(pages)    95% page-level bootstrap interval for a micro ratio sum(num)/sum(den) (e.g. CER = edits/chars).
compare(a, b)             'NOT_DISTINGUISHABLE' if the two intervals overlap, else which is lower.
fmt_rate / fmt_ratio      'value [lo, hi] (n=...)' cells.
"""

from __future__ import annotations

import math
import random

Z = 1.959963984540054
B = 2000
SEED = 20261008


def wilson(k: int, n: int) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = k / n
    den = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / den
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_ratio(pages: list[tuple[float, float]], b: int = B, seed: int = SEED) -> tuple[float, float] | None:
    """pages: [(numerator, denominator)] per page. Resample pages with replacement."""
    pages = [p for p in pages if p[1] > 0]
    if not pages:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(b):
        s = [pages[rng.randrange(len(pages))] for _ in pages]
        stats.append(sum(x for x, _ in s) / sum(y for _, y in s))
    stats.sort()
    return stats[int(0.025 * b)], stats[int(0.975 * b) - 1]


def overlap(a: tuple[float, float] | None, b: tuple[float, float] | None) -> bool:
    return a is None or b is None or not (a[1] < b[0] or b[1] < a[0])


def compare(name_a: str, a, name_b: str, b) -> str:
    if overlap(a, b):
        return "NOT_DISTINGUISHABLE"
    return f"{name_a} < {name_b}" if a[1] < b[0] else f"{name_b} < {name_a}"


def fmt_rate(k: int, n: int) -> str:
    if n <= 0:
        return "n/a (n=0)"
    lo, hi = wilson(k, n)
    return f"{k / n:.3f} [{lo:.3f}, {hi:.3f}] (n={n})"


def fmt_ratio(pages: list[tuple[float, float]], unit: str = "pages") -> str:
    pages = [p for p in pages if p[1] > 0]
    if not pages:
        return "n/a"
    v = sum(x for x, _ in pages) / sum(y for _, y in pages)
    lo, hi = bootstrap_ratio(pages)
    return f"{v:.3f} [{lo:.3f}, {hi:.3f}] (n={len(pages)} {unit})"
