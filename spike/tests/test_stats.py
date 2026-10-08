import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stats import wilson, bootstrap_ratio, compare, fmt_rate, fmt_ratio, overlap

def test_wilson_known_values():
    lo, hi = wilson(0, 10); assert lo == 0.0 and 0.27 < hi < 0.31          # 0/10 -> [0, 0.278]
    lo, hi = wilson(5, 10); assert 0.23 < lo < 0.24 and 0.76 < hi < 0.77   # 5/10 -> [0.237, 0.763]
    assert wilson(0, 0) is None

def test_bootstrap_deterministic_and_contains_point():
    pages = [(10, 100), (30, 100), (5, 50), (40, 120)]
    a, b = bootstrap_ratio(pages), bootstrap_ratio(pages)
    assert a == b and a[0] <= 85 / 370 <= a[1]

def test_compare_overlap():
    assert compare("x", (0.1, 0.2), "y", (0.15, 0.3)) == "NOT_DISTINGUISHABLE"
    assert compare("x", (0.1, 0.2), "y", (0.25, 0.3)) == "x < y"
    assert overlap(None, (0, 1))

def test_formatting():
    assert fmt_rate(5, 10).startswith("0.500 [0.237, 0.763] (n=10)")
    assert "(n=2 pages)" in fmt_ratio([(1, 10), (2, 10)])
