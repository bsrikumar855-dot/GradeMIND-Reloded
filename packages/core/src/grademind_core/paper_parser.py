"""Deterministic question-paper parser (D25 a). Regex only, no LLM (D19). Its output is a DRAFT for the examiner's
structure editor, never trusted as is: every line it cannot place is reported as a warning.

Recognised (one item per line; continuation lines are appended to the current item's text):
- sections:        "SECTION A", "Part B", optionally followed by "Answer any 3 (questions)" -> choose = 3
- questions:       "1.", "1)", "Q1", "Q.1", "Q 1:", "Question 1"
- sub-questions:   "(a)", "a)", "a.", and combined "1(a)" / "1 (a)" / "1.a"
- sub-sub:         "(i)", "ii)", ... (roman; a lone "(i)" right after "(h)" is the letter i)
- marks:           "[5]", "(5)", "(5 marks)", "[2.5 M]", "5 marks", "- 5M" at the end of the line
- OR:              a line that is just "OR" (any case, optional dashes): the next item at the same level - numbered
                   or not - becomes an alternative of the previous one (an OR group with choose = 1)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

_ROMAN = r"(?:x{0,3})(?:ix|iv|v?i{0,3})"
RE_SECTION = re.compile(r"^(?:section|part)\s+([A-Za-z0-9]{1,3})\b[\s:.\-–—]*(.*)$", re.I)
RE_ANY = re.compile(r"\banswer\s+any\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
RE_OR = re.compile(r"^[\s\-–—_*]*or[\s\-–—_*]*$", re.I)
RE_Q = re.compile(r"^(?:q(?:uestion)?\s*\.?\s*(\d{1,3})|(\d{1,3}))\s*(?:[.):]|(?=\s*\(?[a-z]\)|\s*\.[a-z]\b))?\s*(.*)$", re.I)
RE_LETTER = re.compile(r"^\(?([a-h]|[j-z])[).]\s*(.*)$|^\(([a-z])\)\s*(.*)$", re.I)
RE_ROMAN = re.compile(rf"^\(?({_ROMAN})\)\s*(.*)$|^({_ROMAN})[.)]\s+(.*)$", re.I)
RE_MARKS = re.compile(
    r"(?:[\[(]\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)?\s*[\])]|[-–—]?\s*(\d+(?:\.\d+)?)\s*(?:marks?|m)\b\.?)\s*$",
    re.I,
)
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


@dataclass
class _Node:
    id: str
    label: str
    level: int  # depth: 0 section, 1 question, 2-3 sub-questions
    kind: str = "question"  # section | question | letter | roman
    text: str = ""
    marks: Decimal | None = None
    choose: int | None = None
    children: list[_Node] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "label": self.label, "text": self.text.strip()}
        if self.children:
            d["children"] = [c.as_dict() for c in self.children]
            if self.choose is not None:
                d["choose"] = self.choose
        elif self.marks is not None:
            d["max_marks"] = str(self.marks)
        return d


@dataclass(frozen=True)
class ParseResult:
    draft: dict[str, Any]  # {"total_marks": str, "questions": [...]} for the structure editor (may still be invalid)
    warnings: list[str]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


def _split_marks(text: str) -> tuple[str, Decimal | None]:
    m = RE_MARKS.search(text)
    if not m:
        return text, None
    try:
        return text[: m.start()].rstrip(" \t-–—"), Decimal(m.group(1) or m.group(2))
    except InvalidOperation:  # pragma: no cover - the regex only matches numbers
        return text, None


def parse_paper(text: str) -> ParseResult:
    roots: list[_Node] = []
    stack: list[_Node] = []  # current path: [section?, question, letter, roman]
    warnings: list[str] = []
    used: set[str] = set()
    pending_or = False
    last: _Node | None = None

    def uid(base: str) -> str:
        cand, i = base, 2
        while cand in used:
            cand, i = f"{base}-{i}", i + 1
        used.add(cand)
        return cand

    def parent_for(level: int) -> _Node | None:
        while stack and stack[-1].level >= level:
            stack.pop()
        return stack[-1] if stack else None

    def attach(node: _Node) -> None:
        nonlocal pending_or, last
        parent = parent_for(node.level)
        siblings = parent.children if parent else roots
        if pending_or and siblings:
            prev = siblings[-1]
            if prev.choose == 1 and prev.label.endswith(" OR"):  # extend an existing OR group
                prev.children.append(node)
            elif parent is not None and parent.level > 0 and len(siblings) == 1:
                # "6. (a) ... OR (b) ...": the alternatives are all of question 6, so 6 itself is the choice
                siblings.append(node)
                parent.choose = 1
            else:
                group = _Node(
                    id=uid(prev.id + "-or"),
                    label=prev.label + " OR",
                    level=prev.level,
                    kind=prev.kind,
                    choose=1,
                    children=[prev, node],
                )
                siblings[-1] = group
        else:
            siblings.append(node)
        pending_or = False
        stack.append(node)
        last = node

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if RE_OR.match(line):
            if last is None:
                warnings.append(f"'OR' before any question was ignored: {line!r}")
            else:
                pending_or = True
            continue
        sec = RE_SECTION.match(line)
        if sec:
            any_n = RE_ANY.search(line)
            node = _Node(
                id=uid("sec-" + _slug(sec.group(1))),
                label=f"Section {sec.group(1).upper()}",
                level=0,
                kind="section",
                text=sec.group(2),
            )
            if any_n:
                node.choose = int(WORDS.get(any_n.group(1).lower(), 0) or any_n.group(1))
            stack.clear()
            roots.append(node)
            stack.append(node)
            pending_or = False
            continue
        any_n = RE_ANY.search(line)
        if any_n and stack and stack[-1].level == 0 and not stack[-1].children:
            stack[-1].choose = int(WORDS.get(any_n.group(1).lower(), 0) or any_n.group(1))
            stack[-1].text = (stack[-1].text + " " + line).strip()
            continue

        q = RE_Q.match(line)
        if q and (q.group(1) or q.group(2)):
            num = q.group(1) or q.group(2)
            rest = q.group(3)
            body, marks = _split_marks(rest)
            qnode = _Node(id=uid(f"q{num}"), label=num, level=1)
            attach(qnode)
            sub = re.match(r"^(?:\.\s*)?\(?([a-z])\)\s*(.*)$|^\.([a-z])\s+(.*)$", body, re.I)  # "1(a) ..." / "1.a ..."
            if sub:
                letter = (sub.group(1) or sub.group(3)).lower()
                lnode = _Node(
                    id=uid(f"{qnode.id}{letter}"),
                    label=f"({letter})",
                    level=2,
                    kind="letter",
                    text=sub.group(2) or sub.group(4) or "",
                    marks=marks,
                )
                attach(lnode)
            else:
                qnode.text, qnode.marks = body, marks
            continue

        letter_m = RE_LETTER.match(line)
        roman_m = RE_ROMAN.match(line)
        prev_letter = next((n for n in reversed(stack) if n.kind == "letter"), None)
        roman_num = (roman_m.group(1) or roman_m.group(3) or "").lower() if roman_m else ""
        is_letter_i = roman_num == "i" and prev_letter is not None and prev_letter.label == "(h)"
        if roman_m and roman_num and not is_letter_i:
            num = roman_num
            body, marks = _split_marks(roman_m.group(2) or roman_m.group(4) or "")
            host = next((n for n in reversed(stack) if n.kind in ("question", "letter")), None)
            if host is None:
                warnings.append(f"sub-question with no question above it: {line!r}")
                continue
            attach(
                _Node(id=uid(f"{host.id}-{num}"), label=f"({num})", level=host.level + 1, kind="roman", text=body, marks=marks)
            )
            continue
        if letter_m and (letter_m.group(1) or letter_m.group(3)):
            letter = (letter_m.group(1) or letter_m.group(3)).lower()
            body, marks = _split_marks(letter_m.group(2) or letter_m.group(4) or "")
            host = next((n for n in reversed(stack) if n.kind == "question"), None)
            if host is None:
                warnings.append(f"sub-question with no question above it: {line!r}")
                continue
            attach(_Node(id=uid(f"{host.id}{letter}"), label=f"({letter})", level=2, kind="letter", text=body, marks=marks))
            continue

        if pending_or and last is not None:  # unnumbered alternative after "OR"
            body, marks = _split_marks(line)
            base = last.label.removesuffix(" (alt)")
            attach(
                _Node(
                    id=uid(last.id.split("-alt")[0] + "-alt"),
                    label=base + " (alt)",
                    level=last.level,
                    kind=last.kind,
                    text=body,
                    marks=marks,
                )
            )
            continue
        if last is not None:
            body, marks = _split_marks(line)
            last.text = (last.text + " " + body).strip()
            if marks is not None and last.marks is None and not last.children:
                last.marks = marks
            continue
        warnings.append(f"text before the first question was ignored: {line[:80]!r}")

    def finish(n: _Node, path: str) -> Decimal:
        if not n.children:
            if n.marks is None:
                warnings.append(f"{path}: no marks found for {n.label}; enter them in the editor")
                return Decimal(0)
            return n.marks
        totals = [finish(c, f"{path} > {c.label}") for c in n.children]
        if n.marks is not None:
            if sum(totals, Decimal(0)) != n.marks and n.choose is None:
                warnings.append(f"{path}: {n.label} says {n.marks} marks but its parts add up to {sum(totals, Decimal(0))}")
            n.marks = None
        if n.choose is not None:
            return sum(sorted(totals, reverse=True)[: n.choose], Decimal(0))
        return sum(totals, Decimal(0))

    total = sum((finish(r, r.label) for r in roots), Decimal(0))
    return ParseResult(draft={"total_marks": str(total), "questions": [r.as_dict() for r in roots]}, warnings=warnings)
