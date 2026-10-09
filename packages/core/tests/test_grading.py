"""Paper and rubric validation (D25 a/b): every rule fires on a planted error, and valid documents are clean."""

from __future__ import annotations

from decimal import Decimal as D

import pytest
from pydantic import ValidationError

from grademind_core.grading import (
    Criterion,
    Level,
    Paper,
    PaperNode,
    Policy,
    QuestionRubric,
    Rubric,
    leaves,
    node_max,
    paper_max,
    validate_paper,
    validate_rubric,
)


def lv(i: str, m: str, d: str = "applies when ...") -> Level:
    return Level(id=i, name=i.upper(), marks=D(m), definition=d)


GOOD_PAPER = Paper(
    total_marks=D(10),
    questions=(
        PaperNode(id="q1", label="1", max_marks=D(4)),
        PaperNode(
            id="q2",
            label="2",
            children=(
                PaperNode(id="q2a", label="(a)", max_marks=D(2)),
                PaperNode(
                    id="q2b",
                    label="(b)",
                    choose=1,
                    children=(
                        PaperNode(id="q2bi", label="(i)", max_marks=D(4)),
                        PaperNode(id="q2bii", label="(ii)", max_marks=D(4)),
                    ),
                ),
            ),
        ),
    ),
)
GOOD_RUBRIC = Rubric(
    questions=(
        QuestionRubric(
            qid="q1",
            criteria=(
                Criterion(id="c1", name="Method", levels=(lv("none", "0", ""), lv("part", "1"), lv("full", "2"))),
                Criterion(id="c2", name="Answer", levels=(lv("none", "0", ""), lv("full", "2"))),
            ),
        ),
        QuestionRubric(qid="q2a", criteria=(Criterion(id="c1", name="Def", levels=(lv("none", "0", ""), lv("full", "2", ""))),)),
        QuestionRubric(qid="q2bi", criteria=(Criterion(id="c1", name="X", levels=(lv("none", "0", ""), lv("full", "4", ""))),)),
        QuestionRubric(qid="q2bii", criteria=(Criterion(id="c1", name="X", levels=(lv("none", "0", ""), lv("full", "4", ""))),)),
    )
)


def codes(issues: list) -> set[str]:  # type: ignore[type-arg]
    return {i.code for i in issues}


def test_valid_documents_have_no_issues() -> None:
    assert validate_paper(GOOD_PAPER, D(10)) == []
    assert validate_rubric(GOOD_RUBRIC, GOOD_PAPER, Policy()) == []
    assert paper_max(GOOD_PAPER) == D(10) and node_max(GOOD_PAPER.questions[1]) == D(6)
    assert [n.id for n in leaves(GOOD_PAPER)] == ["q1", "q2a", "q2bi", "q2bii"]


def test_leaf_without_marks_counts_zero() -> None:
    assert node_max(PaperNode(id="x", label="x")) == D(0)


@pytest.mark.parametrize(
    ("paper", "code"),
    [
        (
            Paper(
                total_marks=D(4),
                questions=(PaperNode(id="q1", label="1", max_marks=D(2)), PaperNode(id="q1", label="2", max_marks=D(2))),
            ),
            "duplicate_id",
        ),
        (
            Paper(
                total_marks=D(2),
                questions=(
                    PaperNode(id="q1", label="1", max_marks=D(2), children=(PaperNode(id="a", label="a", max_marks=D(2)),)),
                ),
            ),
            "parent_has_marks",
        ),
        (
            Paper(
                total_marks=D(2),
                questions=(
                    PaperNode(
                        id="q1",
                        label="1",
                        choose=2,
                        children=(PaperNode(id="a", label="a", max_marks=D(1)), PaperNode(id="b", label="b", max_marks=D(1))),
                    ),
                ),
            ),
            "choose_too_large",
        ),
        (
            Paper(
                total_marks=D(3),
                questions=(
                    PaperNode(
                        id="q1",
                        label="1",
                        choose=1,
                        children=(PaperNode(id="a", label="a", max_marks=D(3)), PaperNode(id="b", label="b", max_marks=D(2))),
                    ),
                ),
            ),
            "unequal_alternatives",
        ),
        (Paper(total_marks=D(1), questions=(PaperNode(id="q1", label="1"),)), "leaf_without_marks"),
        (Paper(total_marks=D(1), questions=(PaperNode(id="q1", label="1", max_marks=D(1), choose=1),)), "choose_on_leaf"),
        (Paper(total_marks=D(5), questions=(PaperNode(id="q1", label="1", max_marks=D(4)),)), "total_mismatch"),
        (
            Paper(
                total_marks=D(1),
                questions=(
                    PaperNode(
                        id="a",
                        label="a",
                        children=(
                            PaperNode(
                                id="b",
                                label="b",
                                children=(
                                    PaperNode(
                                        id="c",
                                        label="c",
                                        children=(
                                            PaperNode(
                                                id="d", label="d", children=(PaperNode(id="e", label="e", max_marks=D(1)),)
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            "too_deep",
        ),
    ],
)
def test_paper_rules(paper: Paper, code: str) -> None:
    assert code in codes(validate_paper(paper))


def test_paper_must_match_exam_total() -> None:
    assert "exam_total_mismatch" in codes(validate_paper(GOOD_PAPER, D(20)))


def test_schema_rejects_bad_ids_float_like_marks_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        PaperNode(id="Bad Id", label="1")
    with pytest.raises(ValidationError):
        PaperNode(id="q1", label="1", max_marks=D("1.234"))
    with pytest.raises(ValidationError):
        PaperNode.model_validate({"id": "q1", "label": "1", "max_marks": "1", "unexpected": 1})


def rubric_with(qr: QuestionRubric) -> Rubric:
    return Rubric(questions=(qr,) + GOOD_RUBRIC.questions[1:])


@pytest.mark.parametrize(
    ("qr", "code"),
    [
        (QuestionRubric(qid="q1", criteria=()), "no_criteria"),
        (QuestionRubric(qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("full", "4"),)),)), "too_few_levels"),
        (
            QuestionRubric(
                qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "0", ""), lv("b", "4", ""), lv("c", "2"))),)
            ),
            "not_monotonic",
        ),
        (
            QuestionRubric(qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "1"), lv("b", "4", ""))),)),
            "no_zero_level",
        ),
        (
            QuestionRubric(
                qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "-1"), lv("b", "0", ""), lv("c", "4", ""))),)
            ),
            "negative_not_allowed",
        ),
        (
            QuestionRubric(qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "0", ""), lv("a", "4", ""))),)),
            "duplicate_level",
        ),
        (
            QuestionRubric(
                qid="q1",
                criteria=(Criterion(id="c1", name="A", levels=(lv("a", "0", ""), lv("b", "2", "  "), lv("c", "4", ""))),),
            ),
            "definition_required",
        ),
        (
            QuestionRubric(qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "0", ""), lv("b", "3", ""))),)),
            "max_mismatch",
        ),
        (
            QuestionRubric(qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("a", "-2"), lv("b", "-1"))),)),
            "top_not_positive",
        ),
        (
            QuestionRubric(
                qid="q1",
                criteria=(
                    Criterion(id="c1", name="A", levels=(lv("a", "0", ""), lv("b", "2", ""))),
                    Criterion(id="c1", name="B", levels=(lv("a", "0", ""), lv("b", "2", ""))),
                ),
            ),
            "duplicate_criterion",
        ),
    ],
)
def test_rubric_rules(qr: QuestionRubric, code: str) -> None:
    assert code in codes(validate_rubric(rubric_with(qr), GOOD_PAPER, Policy()))


def test_negative_levels_allowed_with_negative_marking_but_need_definitions() -> None:
    qr = QuestionRubric(
        qid="q1", criteria=(Criterion(id="c1", name="A", levels=(lv("neg", "-1", ""), lv("zero", "0", ""), lv("full", "4", ""))),)
    )
    found = codes(validate_rubric(rubric_with(qr), GOOD_PAPER, Policy(negative_marking=True)))
    assert "negative_not_allowed" not in found and "definition_required" in found


def test_rubric_coverage_rules() -> None:
    missing = Rubric(questions=GOOD_RUBRIC.questions[:3])
    assert "missing_rubric" in codes(validate_rubric(missing, GOOD_PAPER, Policy()))
    dup = Rubric(questions=GOOD_RUBRIC.questions + GOOD_RUBRIC.questions[:1])
    assert "duplicate_question" in codes(validate_rubric(dup, GOOD_PAPER, Policy()))
    parent = Rubric(questions=GOOD_RUBRIC.questions + (QuestionRubric(qid="q2", criteria=()),))
    assert "not_a_leaf" in codes(validate_rubric(parent, GOOD_PAPER, Policy()))
    unknown = Rubric(questions=GOOD_RUBRIC.questions + (QuestionRubric(qid="zz", criteria=()),))
    assert "unknown_question" in codes(validate_rubric(unknown, GOOD_PAPER, Policy()))


def test_issues_carry_paths_and_human_messages() -> None:
    issues = validate_rubric(Rubric(questions=GOOD_RUBRIC.questions[:3]), GOOD_PAPER, Policy())
    (i,) = [x for x in issues if x.code == "missing_rubric"]
    assert i.path == "rubric/q2bii" and "(ii)" in i.message
