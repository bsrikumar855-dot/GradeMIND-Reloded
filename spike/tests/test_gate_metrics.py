"""Gate-metric logic on SYNTHETIC strings (no student data), so CI can always run it."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gate_metrics import align_words, cer, ned, pair_stats, project  # noqa: E402

GT = ["the cat sat", "on the mat", "with 10 dogs"]


def test_projection_identity() -> None:
    lines, _ = project(GT, "\n".join(GT))
    assert lines == GT


def test_projection_omission_and_insertion_stay_on_their_lines() -> None:
    lines, _ = project(GT, "the sat\non the big mat\nwith 10 dogs")
    assert lines == ["the sat", "on the big mat", "with 10 dogs"]


def test_align_words_substitution() -> None:
    assert align_words(["a", "b", "c"], ["a", "x", "c"]) == [["a"], ["x"], ["c"]]


def test_identical_wrong_engines_are_silent_errors() -> None:
    wrong = ["the cat sat", "on the mat", "with 14 dogs"]
    proj = {"X": wrong, "Y": wrong}
    s = pair_stats(["X", "Y"], GT, proj, 0.0)
    assert s["agree_lines"] == 3 and s["silent_errors"] == 1 and s["gate_recall"] == 0.0


def test_independent_correct_engine_catches_error() -> None:
    proj = {"X": ["the cat sat", "on the mat", "with 14 dogs"], "Y": GT}
    s = pair_stats(["X", "Y"], GT, proj, 0.0)
    assert s["silent_errors"] == 0 and s["gate_recall"] == 1.0 and s["gate_cost"] == 0.0


def test_tau_tolerates_small_differences() -> None:
    assert ned("on the mat", "on the mat.") <= 0.10 and cer("on the mat", "on the mat.") <= 0.10
    proj = {"X": GT, "Y": ["the cat sat", "on the mat.", "with 10 dogs"]}
    # tau=0: Y's trailing '.' makes it wrong -> the disagreement counts toward recall, not cost
    s0 = pair_stats(["X", "Y"], GT, proj, 0.0)
    assert s0["gate_recall"] == 1.0 and s0["gate_cost"] == 0.0
    # tau=0.10: both within tolerance of GT and of each other -> agree, nothing flagged
    s1 = pair_stats(["X", "Y"], GT, proj, 0.10)
    assert s1["agree_lines"] == 3 and s1["gate_cost"] == 0.0


def test_gate_cost_when_both_correct_but_apart() -> None:
    gt = ["with ten big dogs"]
    proj = {"X": ["with ten big dogs."], "Y": ["with ten big dog"]}  # each 1/17 from GT; 2/18 from each other
    s = pair_stats(["X", "Y"], gt, proj, 0.10)
    assert s["lines_all_correct"] == 1 and s["gate_cost"] == 1.0
    assert pair_stats(["X", "Y"], gt, proj, 0.0)["gate_cost"] is None  # no line is all-correct at tau=0
