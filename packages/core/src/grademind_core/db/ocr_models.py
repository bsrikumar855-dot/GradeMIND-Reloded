"""OCR-derived tables (D28). A module of their own so an import contract can PROVE that nothing in the grading path (the
ScoreComputer, evaluations, the grading routes) can read machine-read text: see the "OCR text never reaches a verdict" contract
in pyproject.toml.

ocr_runs / ocr_lines are display data; line_corrections are the examiner's corrections to them (D19: labelled data for a
future model). All three are append-only (I8) via the grademind_forbid_mutation() trigger.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from grademind_core.db.models import Base, ConsentScope, _created, _pk


class OcrRun(Base):
    """One attempt to machine-read one page with one engine configuration (D28: display-only data, never an input to a
    verdict). Append-only (I8). At most ONE successful run per (page, engine config): a duplicate or concurrent run cannot
    double-write, and a changed model/weight/setting (a different config_hash) legitimately gets its own run. Failed runs
    are kept as history and never block grading."""

    __tablename__ = "ocr_runs"
    __table_args__ = (
        Index("uq_ocr_runs_ok_page_config", "page_id", "config_hash", unique=True, postgresql_where=text("status = 'OK'")),
    )
    id: Mapped[uuid.UUID] = _pk()
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="OK")  # OK | FAILED
    error: Mapped[str | None] = mapped_column(Text)  # safe reason code + message, never student text
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")  # sha256 of the resolved config
    resolved: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    model_names: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # rule 12: resolved, not requested
    weights_sha256: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    component_version: Mapped[str] = mapped_column(String(40), nullable=False)
    image_width: Mapped[int | None] = mapped_column(Integer)  # the pixel space of the line boxes
    image_height: Mapped[int | None] = mapped_column(Integer)
    latency_s: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created()


class OcrLine(Base):
    """One machine-read line of one run: text, box (page pixels) and the engine's score. Append-only (I8)."""

    __tablename__ = "ocr_lines"
    __table_args__ = (UniqueConstraint("ocr_run_id", "line_no"),)
    id: Mapped[uuid.UUID] = _pk()
    ocr_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ocr_runs.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    polygon: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    bbox: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float | None] = mapped_column(Float)


class LineCorrection(Base):
    """D19: every examiner OCR correction = one immutable labelled training example (ARCHITECTURE §5). Append-only (I8)."""

    __tablename__ = "line_corrections"
    id: Mapped[uuid.UUID] = _pk()
    created_at: Mapped[datetime] = _created()
    examiner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id"), nullable=False, index=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False, index=True)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id"), nullable=False)
    ocr_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ocr_lines.id"))  # null when the examiner adds a missed line
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    crop_object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    crop_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    crop_bbox: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    crop_polygon: Mapped[list[Any] | None] = mapped_column(JSONB)
    page_image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(40), nullable=False)
    ocr_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    ocr_model_names: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ocr_weights_sha256: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ocr_text: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_text: Mapped[str] = mapped_column(Text, nullable=False)  # literal: misspellings preserved, [?] allowed
    edit_ops: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    consent_scope: Mapped[ConsentScope] = mapped_column(
        Enum(ConsentScope, name="consent_scope", values_callable=lambda e: [x.value for x in e], create_type=False),
        nullable=False,
    )
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("line_corrections.id"))
