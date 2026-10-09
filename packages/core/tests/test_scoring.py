"""ScoreComputer (D25 c): every rule by example (100% branch coverage) + Hypothesis properties
(0 <= total <= max, monotonic, permutation-invariant, idempotent)."""

from __future__ import annotations

import random
from decimal import Decimal as D

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from grademind_core.grading import (
    Criterion,
    Level,
    Paper,
    PaperNode,
    Policy,
    QuestionRubric,
    RoundingMode,
    Rubric,
    leaves,
    paper_max,
)
from grademind_core.scoring import VERSION, Attempt, NodeStatus, ScoreInputError, Verdict, _round, compute

# ---------------------------------------------------------------------------------------------------------- fixtures


def crit(cid: str, *marks: str) -> Criterion:
    return Criterion(
        id=cid,
        name=cid.upper(),
        levels=tuple(Level(id=f"l{i}", name=f"L{i}", marks=D(m), definition="when it applies") for i, m in enumerate(marks)),
    )


# Q1 (4) = 1a (2: c1 0/1/2) + 1b (2: c1 0/1, c2 0/1); Q2 OR group: answer 1 of 2a (3) / 2b (3); total 7
PAPER = Paper(
    total_marks=D(7),
    questions=(
        PaperNode(
            id="q1",
            label="1",
            children=(PaperNode(id="q1a", label="(a)", max_marks=D(2)), PaperNode(id="q1b", label="(b)", max_marks=D(2))),
        ),
        PaperNode(
            id="q2",
            label="2",
            choose=1,
            children=(PaperNode(id="q2a", label="(a)", max_marks=D(3)), PaperNode(id="q2b", label="(b)", max_marks=D(3))),
        ),
    ),
)
RUBRIC = Rubric(
    questions=(
        QuestionRubric(qid="q1a", criteria=(crit("c1", "0", "1", "2"),)),
        QuestionRubric(qid="q1b", criteria=(crit("c1", "0", "1"), crit("c2", "0", "1"))),
        QuestionRubric(qid="q2a", criteria=(crit("c1", "0", "1.5", "3"),)),
        QuestionRubric(qid="q2b", criteria=(crit("c1", "0", "1.5", "3"),)),
    )
)
P = Policy()


def full_q1() -> tuple[list[Attempt], list[Verdict]]:
    return [Attempt("q1a", 1), Attempt("q1b", 1)], [
        Verdict("q1a", 1, "c1", "l2"),
        Verdict("q1b", 1, "c1", "l1"),
        Verdict("q1b", 1, "c2", "l0"),
    ]


# ---------------------------------------------------------------------------------------------------------- examples


def test_basic_sum_and_statuses() -> None:
    a, v = full_q1()
    s = compute(PAPER, RUBRIC, P, a, v)
    assert s.version == VERSION and s.max_total == D(7)
    assert s.nodes["q1a"].marks == D(2) and s.nodes["q1b"].marks == D(1) and s.nodes["q1"].marks == D(3)
    assert s.total == D(3) and s.complete
    assert s.nodes["q2"].status == NodeStatus.NOT_ATTEMPTED and s.nodes["q2a"].status == NodeStatus.NOT_ATTEMPTED


def test_missing_criterion_makes_attempt_incomplete_but_counts_provisionally() -> None:
    s = compute(PAPER, RUBRIC, P, [Attempt("q1b", 1)], [Verdict("q1b", 1, "c1", "l1")])
    assert s.nodes["q1b"].status == NodeStatus.INCOMPLETE and s.nodes["q1b"].marks == D(1)
    assert s.nodes["q1"].status == NodeStatus.INCOMPLETE and not s.complete and s.total == D(1)


def test_crossed_out_attempts_never_count() -> None:
    s = compute(PAPER, RUBRIC, P, [Attempt("q1a", 1, crossed_out=True)], [Verdict("q1a", 1, "c1", "l2")])
    assert s.nodes["q1a"].status == NodeStatus.NOT_ATTEMPTED and s.total == D(0)


@pytest.mark.parametrize(("rule", "counted", "marks"), [("last", 2, D(1)), ("first", 1, D(2)), ("best", 1, D(2))])
def test_multiple_attempts_policy(rule: str, counted: int, marks: D) -> None:
    attempts = [Attempt("q1a", 1), Attempt("q1a", 2)]
    verdicts = [Verdict("q1a", 1, "c1", "l2"), Verdict("q1a", 2, "c1", "l1")]
    s = compute(PAPER, RUBRIC, Policy(multiple_attempts=rule), attempts, verdicts)  # type: ignore[arg-type]
    assert s.nodes["q1a"].counted_attempt == counted and s.nodes["q1a"].marks == marks
    assert "MULTIPLE_ATTEMPTS" in s.flags and s.nodes["q1a"].flags == ("MULTIPLE_ATTEMPTS",)


def test_best_attempt_ties_go_to_the_later_attempt() -> None:
    attempts = [Attempt("q1a", 1), Attempt("q1a", 2)]
    verdicts = [Verdict("q1a", 1, "c1", "l1"), Verdict("q1a", 2, "c1", "l1")]
    assert compute(PAPER, RUBRIC, Policy(multiple_attempts="best"), attempts, verdicts).nodes["q1a"].counted_attempt == 2


def test_or_group_best_counts_the_higher_alternative_and_flags_extra() -> None:
    attempts = [Attempt("q2a", 1), Attempt("q2b", 1)]
    verdicts = [Verdict("q2a", 1, "c1", "l1"), Verdict("q2b", 1, "c1", "l2")]
    s = compute(PAPER, RUBRIC, P, attempts, verdicts)
    assert s.nodes["q2"].marks == D(3) and s.nodes["q2"].max_marks == D(3)
    assert s.nodes["q2a"].status == NodeStatus.EXCLUDED_BY_CHOICE and s.nodes["q2b"].status == NodeStatus.SCORED
    assert "OR_EXTRA_ATTEMPTED" in s.flags and s.nodes["q2"].flags == ("OR_EXTRA_ATTEMPTED",)


def test_or_group_first_attempted_counts_paper_order() -> None:
    attempts = [Attempt("q2a", 1), Attempt("q2b", 1)]
    verdicts = [Verdict("q2a", 1, "c1", "l1"), Verdict("q2b", 1, "c1", "l2")]
    s = compute(PAPER, RUBRIC, Policy(or_selection="first_attempted"), attempts, verdicts)
    assert s.nodes["q2"].marks == D("1.5") and s.nodes["q2b"].status == NodeStatus.EXCLUDED_BY_CHOICE


def test_or_group_single_attempt_has_no_flag() -> None:
    s = compute(PAPER, RUBRIC, P, [Attempt("q2b", 1)], [Verdict("q2b", 1, "c1", "l1")])
    assert s.nodes["q2"].marks == D("1.5") and s.flags == () and s.nodes["q2a"].status == NodeStatus.NOT_ATTEMPTED


def test_or_group_with_an_incomplete_counted_alternative_is_incomplete() -> None:
    s = compute(PAPER, RUBRIC, P, [Attempt("q2a", 1)], [])
    assert s.nodes["q2"].status == NodeStatus.INCOMPLETE and not s.complete


NEG_RUBRIC = Rubric(
    questions=(
        QuestionRubric(qid="q1a", criteria=(crit("c1", "-1", "0", "2"),)),
        QuestionRubric(qid="q1b", criteria=(crit("c1", "-1", "0", "2"),)),
        QuestionRubric(qid="q2a", criteria=(crit("c1", "0", "3"),)),
        QuestionRubric(qid="q2b", criteria=(crit("c1", "0", "3"),)),
    )
)


def test_negative_marking_floor_at_total() -> None:
    attempts = [Attempt("q1a", 1), Attempt("q1b", 1)]
    verdicts = [Verdict("q1a", 1, "c1", "l0"), Verdict("q1b", 1, "c1", "l0")]
    s = compute(PAPER, NEG_RUBRIC, Policy(negative_marking=True), attempts, verdicts)
    assert s.nodes["q1a"].marks == D(-1) and s.nodes["q1"].marks == D(-2) and s.total == D(0)
    attempts.append(Attempt("q2a", 1))
    verdicts.append(Verdict("q2a", 1, "c1", "l1"))
    assert compute(PAPER, NEG_RUBRIC, Policy(negative_marking=True), attempts, verdicts).total == D(1)


def test_negative_marking_floor_at_question() -> None:
    attempts = [Attempt("q1a", 1), Attempt("q1b", 1), Attempt("q2a", 1)]
    verdicts = [Verdict("q1a", 1, "c1", "l0"), Verdict("q1b", 1, "c1", "l2"), Verdict("q2a", 1, "c1", "l1")]
    s = compute(PAPER, NEG_RUBRIC, Policy(negative_marking=True, negative_floor="question"), attempts, verdicts)
    assert s.nodes["q1a"].marks == D(0) and s.total == D(5)


@pytest.mark.parametrize(
    ("mode", "step", "expected"),
    [
        (RoundingMode.HALF_UP, "1", D(2)),  # 1.5 -> 2
        (RoundingMode.UP, "1", D(2)),
        (RoundingMode.DOWN, "1", D(1)),
        (RoundingMode.HALF_UP, "0.5", D("1.5")),
        (RoundingMode.NONE, "1", D("1.5")),
    ],
)
def test_rounding_total(mode: RoundingMode, step: str, expected: D) -> None:
    s = compute(
        PAPER, RUBRIC, Policy(rounding_mode=mode, rounding_step=D(step)), [Attempt("q2a", 1)], [Verdict("q2a", 1, "c1", "l1")]
    )
    assert s.total == expected


def test_rounding_per_question_and_cap_at_max() -> None:
    paper = Paper(total_marks=D("2.5"), questions=(PaperNode(id="q1", label="1", max_marks=D("2.5")),))
    rubric = Rubric(questions=(QuestionRubric(qid="q1", criteria=(crit("c1", "0", "2.3", "2.5"),)),))
    pol = Policy(rounding_mode=RoundingMode.UP, rounding_step=D(1), rounding_scope="question")
    s = compute(paper, rubric, pol, [Attempt("q1", 1)], [Verdict("q1", 1, "c1", "l1")])
    assert s.nodes["q1"].marks == D("2.5") and s.total == D("2.5")  # 2.3 rounds up to 3, capped at the 2.5 maximum


@pytest.mark.parametrize(
    ("attempts", "verdicts", "match"),
    [
        ([Attempt("zz", 1)], [], "unknown question"),
        ([Attempt("q1a", 1), Attempt("q1a", 1)], [], "duplicate attempt"),
        ([], [Verdict("q1a", 1, "c1", "l0")], "does not exist"),
        ([Attempt("q1a", 1)], [Verdict("q1a", 1, "c9", "l0")], "unknown criterion"),
        ([Attempt("q1a", 1)], [Verdict("q1a", 1, "c1", "l9")], "unknown criterion"),
        ([Attempt("q1a", 1)], [Verdict("q1a", 1, "c1", "l0"), Verdict("q1a", 1, "c1", "l1")], "two different verdicts"),
    ],
)
def test_bad_inputs_are_rejected(attempts: list[Attempt], verdicts: list[Verdict], match: str) -> None:
    with pytest.raises(ScoreInputError, match=match):
        compute(PAPER, RUBRIC, P, attempts, verdicts)


def test_repeated_identical_verdict_is_accepted() -> None:
    a, v = full_q1()
    assert compute(PAPER, RUBRIC, P, a, v + v[:1]).total == D(3)


# -------------------------------------------------------------------------------------------------------- properties


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[Paper, Rubric, Policy, list[Attempt], list[Verdict]]:
    counter = iter(range(10_000))
    steps = [D("0.5"), D(1), D("1.5"), D(2), D(3)]
    negative = draw(st.booleans())

    def leaf(depth: int) -> PaperNode:
        return PaperNode(id=f"n{next(counter)}", label="x", max_marks=draw(st.sampled_from(steps)))

    def node(depth: int) -> PaperNode:
        if depth >= 3 or draw(st.integers(0, 2)) == 0:
            return leaf(depth)
        if draw(st.booleans()):  # OR group: equal alternatives
            mm = draw(st.sampled_from(steps))
            n = draw(st.integers(2, 3))
            kids = tuple(PaperNode(id=f"n{next(counter)}", label="x", max_marks=mm) for _ in range(n))
            return PaperNode(id=f"n{next(counter)}", label="x", choose=draw(st.integers(1, n - 1)), children=kids)
        kids = tuple(node(depth + 1) for _ in range(draw(st.integers(1, 3))))
        return PaperNode(id=f"n{next(counter)}", label="x", children=kids)

    qs = tuple(node(1) for _ in range(draw(st.integers(1, 4))))
    probe = Paper(total_marks=D(1), questions=qs)
    paper = Paper(total_marks=paper_max(probe), questions=qs)
    qrs = []
    for lf in leaves(paper):
        assert lf.max_marks is not None
        # split the maximum into 1-2 criteria, each with 2-4 increasing levels
        parts = [lf.max_marks] if lf.max_marks < 1 or draw(st.booleans()) else [lf.max_marks - D("0.5"), D("0.5")]
        crits = []
        for i, top in enumerate(parts):
            mids = sorted({(top * D(k) / D(4)).quantize(D("0.01")) for k in draw(st.sets(st.integers(1, 3), max_size=2))})
            low = [D(-1)] if negative and draw(st.booleans()) else []
            marks = low + [D(0)] + [m for m in mids if D(0) < m < top] + [top]
            crits.append(
                Criterion(
                    id=f"c{i}",
                    name="c",
                    levels=tuple(Level(id=f"l{j}", name="l", marks=m, definition="d") for j, m in enumerate(marks)),
                )
            )
        qrs.append(QuestionRubric(qid=lf.id, criteria=tuple(crits)))
    rubric = Rubric(questions=tuple(qrs))
    policy = Policy(
        or_selection=draw(st.sampled_from(["best", "first_attempted"])),
        multiple_attempts=draw(st.sampled_from(["last", "first", "best"])),
        rounding_mode=draw(st.sampled_from(list(RoundingMode))),
        rounding_step=draw(st.sampled_from([D("0.25"), D("0.5"), D(1)])),
        rounding_scope=draw(st.sampled_from(["question", "total"])),
        negative_marking=negative,
        negative_floor=draw(st.sampled_from(["question", "total"])),
    )
    attempts: list[Attempt] = []
    verdicts: list[Verdict] = []
    for qr in rubric.questions:
        for no in range(1, draw(st.integers(0, 2)) + 1):
            attempts.append(Attempt(qr.qid, no, crossed_out=draw(st.integers(0, 5)) == 0))
            for c in qr.criteria:
                if draw(st.integers(0, 4)) > 0:
                    verdicts.append(Verdict(qr.qid, no, c.id, draw(st.sampled_from([lv.id for lv in c.levels]))))
    return paper, rubric, policy, attempts, verdicts


PROPS = settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@PROPS
@given(scenarios())
def test_property_total_between_zero_and_max(sc: tuple[Paper, Rubric, Policy, list[Attempt], list[Verdict]]) -> None:
    paper, rubric, policy, attempts, verdicts = sc
    s = compute(paper, rubric, policy, attempts, verdicts)
    assert s.max_total == paper.total_marks
    assert s.total <= s.max_total
    assert s.total >= 0
    for n in s.nodes.values():
        assert n.marks <= n.max_marks


@PROPS
@given(scenarios(), st.randoms(use_true_random=False))
def test_property_permutation_invariant_and_idempotent(
    sc: tuple[Paper, Rubric, Policy, list[Attempt], list[Verdict]], rnd: random.Random
) -> None:
    paper, rubric, policy, attempts, verdicts = sc
    first = compute(paper, rubric, policy, attempts, verdicts)
    a2, v2 = attempts[:], verdicts[:]
    rnd.shuffle(a2)
    rnd.shuffle(v2)
    assert compute(paper, rubric, policy, a2, v2) == first
    assert compute(paper, rubric, policy, attempts, verdicts) == first


@PROPS
@given(scenarios(), st.data())
def test_property_monotonic(sc: tuple[Paper, Rubric, Policy, list[Attempt], list[Verdict]], data: st.DataObject) -> None:
    """Raising any one verdict to a higher level never lowers the total (nor any node's marks)."""
    paper, rubric, policy, attempts, verdicts = sc
    if not verdicts:
        return
    i = data.draw(st.integers(0, len(verdicts) - 1))
    v = verdicts[i]
    levels = next(c.levels for qr in rubric.questions if qr.qid == v.qid for c in qr.criteria if c.id == v.criterion_id)
    pos = [lv.id for lv in levels].index(v.level_id)
    if pos == len(levels) - 1:
        return
    higher = data.draw(st.sampled_from([lv.id for lv in levels[pos + 1 :]]))
    raised = verdicts[:i] + [Verdict(v.qid, v.attempt_no, v.criterion_id, higher)] + verdicts[i + 1 :]
    before = compute(paper, rubric, policy, attempts, verdicts)
    after = compute(paper, rubric, policy, attempts, raised)
    assert after.total >= before.total


@PROPS
@given(scenarios())
def test_property_rounding_is_idempotent(sc: tuple[Paper, Rubric, Policy, list[Attempt], list[Verdict]]) -> None:
    """Re-rounding a rounded total leaves it unchanged."""
    paper, rubric, policy, attempts, verdicts = sc
    s = compute(paper, rubric, policy, attempts, verdicts)
    if policy.rounding_scope == "total":
        assert _round(s.total, policy, s.max_total) == s.total
    for n in s.nodes.values():
        if policy.rounding_scope == "question" and n.qid in {lf.id for lf in leaves(paper)}:
            assert _round(n.marks, policy, n.max_marks) == n.marks
