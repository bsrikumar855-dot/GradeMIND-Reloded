"""4.3 analytics core: pure aggregation, the n < 5 rule, and the shape of the report. No database."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from grademind_core.analytics import MIN_N, AnalyticsInput, BookletData, EvalFact, build_report
from grademind_core.db.models import PaperVersion, RubricVersion
from grademind_core.evaluation import GradingContext
from grademind_core.grading import Paper, Policy, Rubric

PAPER = Paper.model_validate(
    {
        "total_marks": "4",
        "questions": [{"id": "q1", "label": "1", "max_marks": "2"}, {"id": "q2", "label": "2", "max_marks": "2"}],
    }
)
RUBRIC = Rubric.model_validate(
    {
        "questions": [
            {
                "qid": q,
                "criteria": [
                    {
                        "id": "c1",
                        "name": "C",
                        "levels": [
                            {"id": "none", "name": "None", "marks": "0"},
                            {"id": "part", "name": "Part", "marks": "1", "definition": "half"},
                            {"id": "full", "name": "Full", "marks": "2"},
                        ],
                    }
                ],
            }
            for q in ("q1", "q2")
        ]
    }
)
CTX = GradingContext(RubricVersion(version_no=1), PaperVersion(version_no=1), PAPER, RUBRIC, Policy())
LEVEL = {Decimal(0): "none", Decimal(1): "part", Decimal(2): "full"}


def node(marks: str, status: str = "SCORED", attempt: int | None = 1) -> dict[str, Any]:
    return {"marks": marks, "max_marks": "2", "status": status, "counted_attempt": attempt, "flags": []}


def booklet(q1: str | None, q2: str | None = None, finalized: bool = False) -> BookletData:
    sheet: dict[str, dict[str, Any]] = {}
    verdicts: dict[tuple[str, int], dict[str, str]] = {}
    for qid, m in (("q1", q1), ("q2", q2)):
        if m is None:
            sheet[qid] = node("0", "NOT_ATTEMPTED", None)
        else:
            sheet[qid] = node(m)
            verdicts[(qid, 1)] = {"c1": LEVEL[Decimal(m)]}
    return BookletData(uuid.uuid4(), finalized, True, True, sheet, verdicts)


def report(booklets: list[BookletData], evals: list[EvalFact] | None = None, secs: list[float] | None = None) -> dict[str, Any]:
    return build_report(CTX, AnalyticsInput(booklets, evals or [], secs or []), "all", 1, "score-computer-test")


def q(rep: dict[str, Any], qid: str) -> dict[str, Any]:
    (row,) = [x for x in rep["questions"] if x["qid"] == qid]
    out: dict[str, Any] = row
    return out


def test_below_five_observations_nothing_derived_is_shown_but_the_counts_are() -> None:
    r = report([booklet("2"), booklet("2"), booklet("1"), booklet("0")])  # n = 4
    row = q(r, "q1")
    assert row["n"] == 4 and row["suppressed"] is True
    assert row["mean"] is None and row["median"] is None
    assert [(d["marks"], d["count"], d["share"]) for d in row["distribution"]] == [("0", 1, None), ("1", 1, None), ("2", 2, None)]
    crit = row["criteria"][0]
    assert crit["n"] == 4 and crit["suppressed"] and all(lv["share"] is None for lv in crit["levels"])
    assert [(lv["id"], lv["count"]) for lv in crit["levels"]] == [("none", 1), ("part", 1), ("full", 2)]


def test_at_five_observations_the_statistics_appear() -> None:
    r = report([booklet("2"), booklet("2"), booklet("1"), booklet("0"), booklet("1")])  # n = 5, sum 6
    row = q(r, "q1")
    assert row["n"] == MIN_N == 5 and row["suppressed"] is False
    assert row["mean"] == "1.2" and row["median"] == "1"
    assert [(d["marks"], d["count"], d["share"]) for d in row["distribution"]] == [
        ("0", 1, "0.2000"),
        ("1", 2, "0.4000"),
        ("2", 2, "0.4000"),
    ]
    assert [(lv["id"], lv["share"]) for lv in row["criteria"][0]["levels"]] == [
        ("none", "0.2000"),
        ("part", "0.4000"),
        ("full", "0.4000"),
    ]


def test_not_attempted_and_incomplete_are_counted_apart_and_never_enter_the_marks() -> None:
    rows = [booklet("2", None) for _ in range(5)]
    inc = booklet("1", "1")
    inc.sheet["q2"] = node("1", "INCOMPLETE")
    inc.complete = False
    r = report([*rows, inc])
    q2 = q(r, "q2")
    assert q2["n"] == 0 and q2["not_attempted"] == 5 and q2["incomplete"] == 1 and q2["mean"] is None
    assert r["ungraded"] == {"mapped_but_ungraded_answers": 1, "booklets_with_ungraded": 1}
    assert r["booklets"] == {"total": 6, "finalized": 0, "not_started": 0, "in_progress": 1, "complete": 5}


def test_override_rate_and_time_to_grade_follow_the_same_rule() -> None:
    four = [EvalFact("q1", i == 0) for i in range(4)]
    r = report([], four, [10, 20, 30, 40])
    assert r["overrides"] == {"grades_saved": 4, "overrides": 1, "rate": None, "suppressed": True}
    assert r["time_to_grade"]["median_seconds"] is None and r["time_to_grade"]["n"] == 4
    five = [EvalFact("q1", i < 2) for i in range(5)]
    r = report([], five, [10, 20, 30, 40, 100])
    assert r["overrides"] == {"grades_saved": 5, "overrides": 2, "rate": "0.4000", "suppressed": False}
    assert r["time_to_grade"]["median_seconds"] == 30 and "not working time" in r["time_to_grade"]["note"]


def test_only_the_counted_attempt_feeds_the_criterion_counts() -> None:
    b = booklet("2")
    b.sheet["q1"] = node("2", attempt=2)  # the counted attempt is the second one
    b.verdicts = {("q1", 1): {"c1": "none"}, ("q1", 2): {"c1": "full"}}
    r = report([b])
    levels = {lv["id"]: lv["count"] for lv in q(r, "q1")["criteria"][0]["levels"]}
    assert levels == {"none": 0, "part": 0, "full": 1}


@settings(max_examples=60, deadline=None)
@given(st.lists(st.sampled_from(["0", "1", "2"]), min_size=0, max_size=30))
def test_properties_counts_add_up_and_nothing_is_shown_under_five(marks: list[str]) -> None:
    r = report([booklet(m) for m in marks])
    row = q(r, "q1")
    n = len(marks)
    assert row["n"] == n == sum(d["count"] for d in row["distribution"])
    crit = row["criteria"][0]
    assert crit["n"] == n == sum(lv["count"] for lv in crit["levels"])
    if n < MIN_N:
        assert row["mean"] is None and row["median"] is None
        assert all(d["share"] is None for d in row["distribution"]) and all(lv["share"] is None for lv in crit["levels"])
    else:
        values = [Decimal(m) for m in marks]
        assert min(values) <= Decimal(row["mean"]) <= max(values) and min(values) <= Decimal(row["median"]) <= max(values)
        assert abs(sum(Decimal(d["share"]) for d in row["distribution"]) - 1) < Decimal("0.001")
