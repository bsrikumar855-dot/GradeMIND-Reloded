"""Deterministic question-paper parser (D25 a). Synthetic papers only (no real exam content)."""

from __future__ import annotations

from decimal import Decimal as D
from typing import Any

from grademind_core.grading import Paper, validate_paper
from grademind_core.paper_parser import parse_paper


def ids(nodes: list[dict[str, Any]]) -> list[Any]:
    return [(n["id"], ids(n["children"])) if "children" in n else n["id"] for n in nodes]


UNIVERSITY = """
CONTINUOUS INTERNAL ASSESSMENT - ENVIRONMENTAL SCIENCE
Time: 1 hour                      Max marks: 20
1. Define ecosystem. [2]
2. List any two renewable resources. (2 marks)
3. (a) Explain the water cycle with a neat diagram. [4]
   (b) What is eutrophication? [2]
4. Discuss the causes of air pollution.
   Suggest two control measures. [5 marks]
5. Write short notes on: [5]
"""


def test_university_paper() -> None:
    r = parse_paper(UNIVERSITY)
    qs = r.draft["questions"]
    assert ids(qs) == ["q1", "q2", ("q3", ["q3a", "q3b"]), "q4", "q5"]
    assert [q.get("max_marks") for q in qs] == ["2", "2", None, "5", "5"]
    assert (
        qs[2]["children"][0]["text"] == "Explain the water cycle with a neat diagram."
        and qs[2]["children"][1]["max_marks"] == "2"
    )
    assert qs[3]["text"] == "Discuss the causes of air pollution. Suggest two control measures."  # continuation joined
    assert r.draft["total_marks"] == "20"
    assert any("before the first question" in w for w in r.warnings)  # the header lines are reported, not guessed
    assert validate_paper(Paper.model_validate(r.draft), D(20)) == []


CBSE = """
SECTION A
Q1. Which gas is released in photosynthesis? (1)
Q2. Name the SI unit of force. (1)
SECTION B
Answer any TWO questions.
Q3. State Newton's second law. [3]
Q4. Explain refraction of light. [3]
Q5. Derive v = u + at. [3]
SECTION C
Q6. (a) Describe the structure of the heart. [5]
OR
(b) Describe the human digestive system. [5]
Q7. Explain the process: [6]
(i) evaporation [3]
(ii) condensation [3]
"""


def test_cbse_sections_any_or_and_romans() -> None:
    r = parse_paper(CBSE)
    secs = r.draft["questions"]
    assert [s["label"] for s in secs] == ["Section A", "Section B", "Section C"]
    assert secs[1]["choose"] == 2 and len(secs[1]["children"]) == 3
    q6 = secs[2]["children"][0]
    assert q6["id"] == "q6" and q6["choose"] == 1 and [c["id"] for c in q6["children"]] == ["q6a", "q6b"]
    q7 = secs[2]["children"][1]
    assert [c["label"] for c in q7["children"]] == ["(i)", "(ii)"] and "max_marks" not in q7
    assert r.draft["total_marks"] == str(D(2) + D(6) + D(5) + D(6))  # A 2, B any 2 of 3 x3, C: OR 5 + 6
    assert r.warnings == []


def test_unnumbered_or_alternative_and_or_chains() -> None:
    r = parse_paper("1. Explain X. [3]\nOR\nDescribe Y. [3]\n-- OR --\nOutline Z. [3]\n2. Define W. [2]")
    q = r.draft["questions"]
    assert q[0]["choose"] == 1 and [c["label"] for c in q[0]["children"]] == ["1", "1 (alt)", "1 (alt)"]
    assert len({c["id"] for c in q[0]["children"]}) == 3 and r.draft["total_marks"] == "5"


def test_question_number_formats_and_marks_formats() -> None:
    r = parse_paper("Q.1 A [1]\nQuestion 2: B (2 marks)\n3) C 1.5 M\nQ 4 D - 4 marks\n5 (a) E [1]\n5 (b) F [1]")
    q = r.draft["questions"]
    assert [x["id"] for x in q] == ["q1", "q2", "q3", "q4", "q5", "q5-2"]
    assert [x.get("max_marks") for x in q[:4]] == ["1", "2", "1.5", "4"]


def test_combined_number_letter_and_letter_i_after_h() -> None:
    lines = ["1(a) part a [1]"] + [f"({c}) part {c} [1]" for c in "bcdefgh"] + ["(i) part i [1]"]
    r = parse_paper("\n".join(lines))
    labels = [c["label"] for c in r.draft["questions"][0]["children"]]
    assert labels[-1] == "(i)" and len(labels) == 9  # (i) after (h) is the letter, not roman one
    r2 = parse_paper("1. Q [2]\n(a) A\n(i) one [1]\n(ii) two [1]")
    assert ids(r2.draft["questions"]) == [("q1", [("q1a", ["q1a-i", "q1a-ii"])])]


def test_problems_are_warnings_not_guesses() -> None:
    r = parse_paper("OR\n(a) orphan\n(ii) orphan roman\n1. Has no marks\n2. Parent [10]\n(a) x [2]\n(b) y [3]")
    w = " | ".join(r.warnings)
    assert "'OR' before any question" in w and "no question above it" in w
    assert "no marks found for 1" in w and "says 10 marks but its parts add up to 5" in w
    assert r.draft["questions"][1].get("max_marks") is None  # the parent's own figure is dropped; parts are authoritative


def test_empty_input() -> None:
    r = parse_paper("   \n\n")
    assert r.draft == {"total_marks": "0", "questions": []} and r.warnings == []


def test_section_header_with_answer_any_on_the_same_line() -> None:
    r = parse_paper("PART B - Answer any 2\n1. a [5]\n2. b [5]\n3. c [5]")
    sec = r.draft["questions"][0]
    assert sec["label"] == "Section B" and sec["choose"] == 2 and r.draft["total_marks"] == "10"
