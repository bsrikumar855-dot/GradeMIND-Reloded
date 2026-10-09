"""ORM models: Phase 1 schema (spec §14 subset) plus the D19 line-level correction dataset.

Conventions: UUID primary keys, foreign keys everywhere, timestamps in UTC. Tables listed in APPEND_ONLY_TABLES get a
Postgres trigger in the migration that rejects UPDATE and DELETE (I8). Corrections chain via `supersedes_id`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, validates


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Role(enum.StrEnum):
    ADMIN = "admin"
    EXAMINER = "examiner"
    TEACHER = "teacher"


class ConsentScope(enum.StrEnum):
    """D20: whether data may ever leave the machine / be released publicly."""

    LOCAL_ONLY = "local_only"
    PUBLIC_RELEASE = "public_release"


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class StageStatus(enum.StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


APPEND_ONLY_TABLES = ("line_corrections", "audit_logs", "job_stage_attempts")


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    cloud_llm_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # spec §16: explicit per-org opt-in
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role", values_callable=lambda e: [x.value for x in e]), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = _created()

    @validates("email")
    def _normalise_email(self, _key: str, value: str) -> str:
        return value.strip().lower()  # login looks up lowercase; store lowercase so mixed-case addresses can sign in


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    course: Mapped[str | None] = mapped_column(String(200))
    total_marks: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )  # D4: CBSE / university profile, never hard-coded
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created()


class ExamAssignment(Base):
    """RBAC (spec §16): examiners see only the exams assigned to them."""

    __tablename__ = "exam_assignments"
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True, index=True)
    created_at: Mapped[datetime] = _created()


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("exam_id", "source_sha256", name="uq_submissions_exam_source_sha256"),)
    id: Mapped[uuid.UUID] = _pk()
    exam_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exams.id"), nullable=False, index=True)
    student_ref: Mapped[str] = mapped_column(String(200), nullable=False)  # pseudonymous reference; no names required
    consent_scope: Mapped[ConsentScope] = mapped_column(
        Enum(ConsentScope, name="consent_scope", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=ConsentScope.LOCAL_ONLY,
    )
    source_object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mime: Mapped[str] = mapped_column(String(64), nullable=False)  # sniffed, never the client's claim
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_filename: Mapped[str] = mapped_column(String(200), nullable=False)  # sanitised; display only, never logged
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created()


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("submission_id", "page_no"),)
    id: Mapped[uuid.UUID] = _pk()
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class PageQualityFlag(Base):
    __tablename__ = "page_quality_flags"
    id: Mapped[uuid.UUID] = _pk()
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id"), nullable=False, index=True)
    flag: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # PAGE_BLURRY, PAGE_DETECTION_ANOMALY, ...
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    component_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()


class OcrRun(Base):
    __tablename__ = "ocr_runs"
    id: Mapped[uuid.UUID] = _pk()
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model_names: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # rule 12: resolved, not requested
    weights_sha256: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    component_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()


class OcrLine(Base):
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


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("submissions.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", values_callable=lambda e: [x.value for x in e]),
        nullable=False,
        default=JobStatus.QUEUED,
    )
    current_stage: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # optimistic locking (spec §14)
    __mapper_args__ = {"version_id_col": version}


class JobStageAttempt(Base):
    """Append-only log of stage executions (I8); retries add rows, never edit them."""

    __tablename__ = "job_stage_attempts"
    __table_args__ = (UniqueConstraint("seq", name="uq_job_stage_attempts_seq"),)
    id: Mapped[uuid.UUID] = _pk()
    # monotonic insertion order: the SSE event id. created_at cannot order rows (now() is the transaction start time)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("processing_jobs.id"), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StageStatus] = mapped_column(
        Enum(StageStatus, name="stage_status", values_callable=lambda e: [x.value for x in e]), nullable=False
    )
    cache_key: Mapped[str | None] = mapped_column(String(128), index=True)  # content hash + component version
    component_version: Mapped[str] = mapped_column(String(40), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    output_ref: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _pk()
    at: Mapped[datetime] = _created()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
