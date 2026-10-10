"""Examiner corrections of machine-read lines (3.4; D19, D28). Labelled data for a future model, and display data only: nothing in
the grading path may import this module (import-linter contract).

A correction never changes the OCR line or the page image. It appends a row to the append-only `line_corrections` table with the
original machine text, the corrected text, the character-level edit operations, a crop of the line, and the provenance (which
engine read it, which page image, which examiner, which consent scope). Corrections of corrections chain through `supersedes_id`.
"""

from __future__ import annotations

import difflib
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from grademind_core.db.ocr_models import LineCorrection

MAX_LINE_CHARS = 2000
# characters that make text look different from what it is, or that cannot be shown: C0/C1 controls (a line is one line, so line
# breaks and tabs too), soft hyphen, zero-width and direction-changing characters. Built from code points on purpose.
_FORBIDDEN = [
    *range(0x00, 0x20),
    *range(0x7F, 0xA0),
    0x00AD,
    0x061C,
    0x180E,
    *range(0x200B, 0x2010),
    *range(0x2028, 0x202F),
    *range(0x2060, 0x2070),
    0xFEFF,
    *range(0xFFF9, 0xFFFC),
]
_FORBIDDEN_RE = re.compile("[" + "".join(re.escape(chr(c)) for c in _FORBIDDEN) + "]")


class InvalidLineTextError(ValueError):
    """`code` is a stable reason; `message` is safe to show to the examiner."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def clean_line_text(text: str) -> str:
    """The examiner's text, literally: misspellings and "[?]" for illegible parts are kept; only leading/trailing spaces go.
    An empty result is valid (the machine invented a line that is not there). Anything that would make the text look different
    from what it is is refused, not silently changed."""
    t = text.strip(" ")
    if len(t) > MAX_LINE_CHARS:
        raise InvalidLineTextError("too_long", f"A line can be at most {MAX_LINE_CHARS} characters.")
    if _FORBIDDEN_RE.search(t):
        raise InvalidLineTextError(
            "invalid_characters",
            "The text contains line breaks or invisible / direction-changing characters. Type the line as plain text.",
        )
    return t


def edit_ops(old: str, new: str) -> list[dict[str, Any]]:
    """Character-level edit operations that turn `old` into `new` (difflib opcodes without the unchanged runs), each with its
    position in `old`: [{"op": "replace"|"delete"|"insert", "at": i, "old": "...", "new": "..."}]."""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [
        {"op": tag, "at": i1, "old": old[i1:i2], "new": new[j1:j2]} for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"
    ]


def apply_ops(old: str, ops: list[dict[str, Any]]) -> str:
    """Inverse check used by tests and by the dataset export: replaying the operations on the original gives the correction."""
    out: list[str] = []
    pos = 0
    for o in sorted(ops, key=lambda o: o["at"]):
        out.append(old[pos : o["at"]])
        out.append(o["new"])
        pos = o["at"] + len(o["old"])
    out.append(old[pos:])
    return "".join(out)


def heads(db: Session, line_ids: list[uuid.UUID]) -> dict[uuid.UUID, LineCorrection]:
    """The CURRENT correction of each line that has one: the end of its chain (the row nothing supersedes)."""
    if not line_ids:
        return {}
    rows = list(db.scalars(select(LineCorrection).where(LineCorrection.ocr_line_id.in_(line_ids))))
    superseded = {r.supersedes_id for r in rows if r.supersedes_id is not None}
    return {r.ocr_line_id: r for r in rows if r.id not in superseded and r.ocr_line_id is not None}


def chain(db: Session, line_id: uuid.UUID) -> list[LineCorrection]:
    """Every correction of one line, oldest first (the chain from its first correction to its current one)."""
    rows = list(db.scalars(select(LineCorrection).where(LineCorrection.ocr_line_id == line_id)))
    by_prev = {r.supersedes_id: r for r in rows}
    out: list[LineCorrection] = []
    cur = by_prev.get(None)
    while cur is not None:
        out.append(cur)
        cur = by_prev.get(cur.id)
    return out
