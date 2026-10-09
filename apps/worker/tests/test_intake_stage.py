"""ingest pipeline (INTAKE -> RASTERIZE) against real Postgres + MinIO."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from pdfgen import make_pdf, make_png
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.config import Env, Settings
from grademind_core.db.models import Exam, JobStatus, Organization, Page, ProcessingJob, Role, Submission, User
from grademind_core.jobs import run_job
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_worker.stages import INGEST, PIPELINES

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
S3 = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
pytestmark = pytest.mark.skipif(not (URL and S3), reason="needs GRADEMIND_TEST_DATABASE_URL and GRADEMIND_TEST_S3_ENDPOINT")
CORE = Path(__file__).resolve().parents[3] / "packages" / "core"


@pytest.fixture(scope="module")
def env() -> Iterator[tuple[sessionmaker[Session], ObjectStore]]:
    e = {**os.environ, "GRADEMIND_DATABASE_URL": URL or ""}
    for cmd in (["downgrade", "base"], ["upgrade", "head"]):
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(CORE / "alembic.ini"), *cmd], env=e, check=True, capture_output=True
        )
    engine = create_engine(URL or "")
    store = ObjectStore(
        Settings(
            env=Env.TEST,
            s3_endpoint_url=S3 or "",
            s3_access_key=os.environ.get("GRADEMIND_TEST_S3_ACCESS_KEY", ""),
            s3_secret_key=os.environ.get("GRADEMIND_TEST_S3_SECRET_KEY", ""),
            s3_bucket="gm-test-worker",
        )
    )
    store.ensure_bucket()
    yield sessionmaker(bind=engine, expire_on_commit=False), store
    engine.dispose()


def ingest_job(db: sessionmaker[Session], key: str, sha: str, mime: str = "application/pdf") -> uuid.UUID:
    with db() as s, s.begin():
        org = Organization(name="O")
        s.add(org)
        s.flush()
        u = User(
            org_id=org.id, email=f"w{uuid.uuid4().hex[:8]}@college-one.in", display_name="w", password_hash="x", role=Role.ADMIN
        )
        s.add(u)
        s.flush()
        ex = Exam(org_id=org.id, name="E", subject="S", total_marks=Decimal(10), created_by=u.id)
        s.add(ex)
        s.flush()
        sub = Submission(
            exam_id=ex.id,
            student_ref="S1",
            source_object_key=key,
            source_sha256=sha,
            source_mime=mime,
            source_size_bytes=1,
            source_filename="x.pdf",
            created_by=u.id,
        )
        s.add(sub)
        s.flush()
        job = ProcessingJob(kind=INGEST, submission_id=sub.id, created_by=u.id)
        s.add(job)
        s.flush()
        return job.id


def put(store: ObjectStore, data: bytes) -> str:
    return store.put(ObjectKind.SUBMISSION_SOURCE, io.BytesIO(data), len(data), "application/pdf")


def pages_of(db: sessionmaker[Session], jid: uuid.UUID) -> list[Page]:
    with db() as s:
        job = s.get(ProcessingJob, jid)
        assert job is not None
        return list(s.scalars(select(Page).where(Page.submission_id == job.submission_id).order_by(Page.page_no)))


def status_and_error(db: sessionmaker[Session], jid: uuid.UUID) -> tuple[JobStatus, str | None]:
    with db() as s:
        j = s.get(ProcessingJob, jid)
        assert j is not None
        return j.status, j.error


def test_pdf_booklet_becomes_page_images(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = make_pdf([["page one " + uuid.uuid4().hex], ["page two"], ["page three"]])
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.COMPLETED
    pages = pages_of(db, jid)
    expected = [(n, 1240, 1755, "rasterize-0.1.0") for n in (1, 2, 3)]
    assert [(pg.page_no, pg.width, pg.height, pg.renderer) for pg in pages] == expected
    assert len(pages) == 3 and all(pg.thumb_object_key for pg in pages)
    img = store.get_bytes(pages[0].object_key)
    assert img[:3] == b"\xff\xd8\xff" and hashlib.sha256(img).hexdigest() == pages[0].sha256


def test_image_booklet_is_one_page(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = make_png(800, 1100)
    key = store.put(ObjectKind.SUBMISSION_SOURCE, io.BytesIO(data), len(data), "image/png")
    jid = ingest_job(db, key, hashlib.sha256(data).hexdigest(), mime="image/png")
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.COMPLETED
    assert [(pg.page_no, pg.width, pg.height) for pg in pages_of(db, jid)] == [(1, 800, 1100)]


def test_unreadable_pdf_fails_rasterize_with_a_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = b"%PDF-1.7 this is not really a pdf " + uuid.uuid4().bytes
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.FAILED
    assert status_and_error(db, jid)[1] == "RASTERIZE_FAILED: The PDF could not be opened."


def test_rasterize_resumes_without_duplicating_pages(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = make_pdf([["resume " + uuid.uuid4().hex], ["two"]])
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    with db() as s, s.begin():  # a previous (crashed) run already stored page 1
        job = s.get(ProcessingJob, jid)
        assert job is not None
        s.add(
            Page(
                submission_id=job.submission_id,
                page_no=1,
                object_key="page-image/" + "0" * 32,
                sha256="0" * 64,
                width=1,
                height=1,
            )
        )
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.COMPLETED
    pages = pages_of(db, jid)
    assert (
        [pg.page_no for pg in pages] == [1, 2] and pages[0].width == 1 and pages[1].width == 1240
    )  # page 1 kept, page 2 rendered


def test_same_file_in_two_exams_gets_pages_in_both(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    """Regression: stage caching is global, so the input hash must include the submission."""
    db, store = env
    data = make_pdf([["shared " + uuid.uuid4().hex]])
    sha = hashlib.sha256(data).hexdigest()
    j1, j2 = ingest_job(db, put(store, data), sha), ingest_job(db, put(store, data), sha)
    for j in (j1, j2):
        assert run_job(db, j, PIPELINES, services={"store": store}) == JobStatus.COMPLETED
    assert len(pages_of(db, j1)) == 1 and len(pages_of(db, j2)) == 1


def test_altered_source_fails_with_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = ingest_job(db, put(store, b"%PDF-1.7 altered"), "0" * 64)
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.FAILED
    assert status_and_error(db, jid) == (
        JobStatus.FAILED,
        "INTAKE_FAILED: The stored file does not match the upload. Upload it again.",
    )


def test_missing_source_fails_with_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = ingest_job(db, "submission-source/" + uuid.uuid4().hex, "1" * 64)
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.FAILED
    assert status_and_error(db, jid)[1] == "INTAKE_FAILED: The uploaded file is missing from storage. Upload it again."
