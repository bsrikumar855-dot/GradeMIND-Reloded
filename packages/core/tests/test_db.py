"""DB tests against real Postgres (I8 triggers live in Postgres). Skipped unless GRADEMIND_TEST_DATABASE_URL is set;
CI provides a Postgres service. The schema is built by the real Alembic migration, not create_all."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from grademind_core.db.models import (
    AuditLog,
    ConsentScope,
    Exam,
    JobStageAttempt,
    JobStatus,
    Organization,
    Page,
    ProcessingJob,
    Role,
    StageStatus,
    Submission,
    User,
)
from grademind_core.db.ocr_models import LineCorrection, OcrLine, OcrRun
from grademind_core.db.session import transaction

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="GRADEMIND_TEST_DATABASE_URL not set (DB tests need real Postgres)")
CORE = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def db() -> Iterator[sessionmaker[Session]]:
    env = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=env, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()  # no pooled connection may outlive the module: the next module's downgrade needs the table locks


def seed(s: Session) -> dict[str, uuid.UUID]:
    org = Organization(name="Test Org")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email=f"ex{uuid.uuid4().hex[:8]}@test", display_name="Ex", password_hash="x", role=Role.EXAMINER)
    s.add(u)
    s.flush()
    ex = Exam(org_id=org.id, name="CIA", subject="EVS", total_marks=Decimal("50"), created_by=u.id)
    s.add(ex)
    s.flush()
    sub = Submission(
        exam_id=ex.id,
        student_ref="S1",
        source_object_key="k",
        source_sha256="0" * 64,
        source_mime="application/pdf",
        source_size_bytes=1,
        source_filename="s1.pdf",
        created_by=u.id,
    )
    s.add(sub)
    s.flush()
    pg = Page(submission_id=sub.id, page_no=2, object_key="p", sha256="1" * 64, width=10, height=10)
    s.add(pg)
    s.flush()
    return {"org": org.id, "user": u.id, "exam": ex.id, "sub": sub.id, "page": pg.id}


def correction(ids: dict[str, uuid.UUID], text_: str, supersedes: uuid.UUID | None = None) -> LineCorrection:
    return LineCorrection(
        examiner_id=ids["user"],
        exam_id=ids["exam"],
        submission_id=ids["sub"],
        page_id=ids["page"],
        subject="EVS",
        crop_object_key="c",
        crop_sha256="2" * 64,
        crop_bbox=[0, 0, 5, 5],
        page_image_sha256="1" * 64,
        preprocessing_version="0.1.2",
        ocr_provider="paddle_v6",
        ocr_model_names={"det": "PP-OCRv6_medium_det"},
        ocr_weights_sha256={},
        ocr_text="divesity",
        corrected_text=text_,
        edit_ops=[],
        consent_scope=ConsentScope.LOCAL_ONLY,
        supersedes_id=supersedes,
    )


def test_invariant_I8_line_corrections_are_append_only(db: sessionmaker[Session]) -> None:
    with db() as s, s.begin():
        ids = seed(s)
        c1 = correction(ids, "divesity[?]")
        s.add(c1)
        s.flush()
        s.add(correction(ids, "divesity", supersedes=c1.id))  # a correction of a correction is a NEW row
    for sql in ("UPDATE line_corrections SET corrected_text = 'x'", "DELETE FROM line_corrections"):
        with db() as s, pytest.raises(DBAPIError, match="append-only"):
            s.execute(text(sql))
            s.commit()


@pytest.mark.parametrize("table", ["audit_logs", "job_stage_attempts"])
@pytest.mark.parametrize("op", ["UPDATE {t} SET created_at = created_at", "DELETE FROM {t}"])
def test_invariant_I8_other_append_only_tables(db: sessionmaker[Session], table: str, op: str) -> None:
    # A row-level trigger only fires on existing rows, so insert one first (an empty-table DELETE would prove nothing).
    with db() as s, s.begin():
        ids = seed(s)
        if table == "audit_logs":
            s.add(AuditLog(actor_id=ids["user"], action="test.append_only", entity_type="test"))
        else:
            job = ProcessingJob(kind="ingest", submission_id=ids["sub"], created_by=ids["user"])
            s.add(job)
            s.flush()
            s.add(
                JobStageAttempt(
                    job_id=job.id, stage="INGEST", attempt_no=1, status=StageStatus.STARTED, component_version="0.1.0"
                )
            )
    sql = op.format(t=table).replace(
        "SET created_at = created_at", "SET created_at = created_at" if table == "job_stage_attempts" else "SET at = at"
    )
    with db() as s, pytest.raises(DBAPIError, match="append-only"):
        s.execute(text(sql))
        s.commit()


def test_optimistic_locking_on_jobs(db: sessionmaker[Session]) -> None:
    with db() as s, s.begin():
        ids = seed(s)
        job = ProcessingJob(kind="ingest", submission_id=ids["sub"], created_by=ids["user"])
        s.add(job)
    s1, s2 = db(), db()
    a, b = s1.get(ProcessingJob, job.id), s2.get(ProcessingJob, job.id)
    assert a is not None and b is not None
    a.status = JobStatus.RUNNING
    s1.commit()
    b.status = JobStatus.FAILED
    with pytest.raises(StaleDataError):
        s2.commit()
    s1.close()
    s2.close()


def test_transaction_rolls_back_partial_writes(db: sessionmaker[Session]) -> None:
    with db() as s:
        before = s.execute(text("SELECT count(*) FROM organizations")).scalar_one()
    with pytest.raises(RuntimeError), transaction(URL) as s:
        s.add(Organization(name="half-written"))
        s.flush()
        raise RuntimeError("boom")
    with db() as s:
        assert s.execute(text("SELECT count(*) FROM organizations")).scalar_one() == before


def test_d19_line_correction_schema_contract(db: sessionmaker[Session]) -> None:
    """D26.5: the D19 training-data table exists with crop + corrected text + provenance, all NOT NULL, append-only."""
    required = {
        # crop
        "crop_object_key", "crop_sha256", "crop_bbox", "page_image_sha256", "page_id", "submission_id",
        # text
        "ocr_text", "corrected_text", "edit_ops",
        # provenance
        "examiner_id", "exam_id", "subject", "created_at", "ocr_provider", "ocr_model_names", "ocr_weights_sha256",
        "preprocessing_version", "consent_scope",
    }  # fmt: skip
    with db() as s:
        cols = dict(
            s.execute(
                text("SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'line_corrections'")
            ).all()
        )
        triggers = s.scalars(
            text(
                "SELECT pg_get_triggerdef(oid) FROM pg_trigger WHERE tgrelid = 'line_corrections'::regclass AND NOT tgisinternal"
            )
        ).all()
    assert {c for c in required if cols.get(c) != "NO"} == set()
    assert "supersedes_id" in cols and cols["supersedes_id"] == "YES"  # corrections of corrections chain, never edit
    assert any("BEFORE DELETE OR UPDATE" in t and "grademind_forbid_mutation" in t for t in triggers)


def _line(s: Session, ids: dict[str, uuid.UUID]) -> uuid.UUID:
    run = OcrRun(
        page_id=ids["page"],
        provider="paddle_v6",
        config_hash="c" * 64,
        resolved={},
        model_names={},
        weights_sha256={},
        component_version="t",
    )
    s.add(run)
    s.flush()
    ln = OcrLine(
        ocr_run_id=run.id, line_no=0, polygon=[[0, 0], [5, 0], [5, 5], [0, 5]], bbox=[0, 0, 5, 5], text="divesity", score=0.5
    )
    s.add(ln)
    s.flush()
    return ln.id


def test_a_lines_corrections_form_a_linear_chain(db: sessionmaker[Session]) -> None:
    """3.4: concurrent edits conflict instead of forking a line's history."""
    with db() as s, s.begin():
        ids = seed(s)
        line = _line(s, ids)
        first = correction(ids, "diversity")
        first.ocr_line_id = line
        s.add(first)
        s.flush()
        second = correction(ids, "diversity.", supersedes=first.id)
        second.ocr_line_id = line
        s.add(second)
    # a second FIRST correction of the same line, and a second correction superseding the same predecessor, both fail
    with db() as s, s.begin():
        ids = seed(s)
        line = _line(s, ids)
        a = correction(ids, "a")
        a.ocr_line_id = line
        s.add(a)
        s.flush()
        b = correction(ids, "b")
        b.ocr_line_id = line
        s.add(b)
        with pytest.raises(IntegrityError, match="uq_line_corrections_first_per_line"):
            s.flush()
    with db() as s, s.begin():
        ids = seed(s)
        line = _line(s, ids)
        a = correction(ids, "a")
        a.ocr_line_id = line
        s.add(a)
        s.flush()
        b = correction(ids, "b", supersedes=a.id)
        b.ocr_line_id = line
        s.add(b)
        s.flush()
        c = correction(ids, "c", supersedes=a.id)
        c.ocr_line_id = line
        s.add(c)
        with pytest.raises(IntegrityError, match="uq_line_corrections_supersedes"):
            s.flush()
