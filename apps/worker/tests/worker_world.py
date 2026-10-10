"""Shared worker-test world (fixture + helpers), loaded with `pytest_plugins = ["worker_world"]`.

It is deliberately NOT a conftest.py: apps/api/tests has one too, both are imported under the module name "conftest", and the
API tests import their helpers from it by name, so a second conftest would shadow it when the whole suite runs together."""

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
from pdfgen import make_pdf
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.config import Env, Settings
from grademind_core.db.models import Exam, JobStatus, Organization, Page, ProcessingJob, Role, Submission, User
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_worker.stages import INGEST

URL = os.environ.get("GRADEMIND_TEST_DATABASE_URL")
S3 = os.environ.get("GRADEMIND_TEST_S3_ENDPOINT")
needs_stack = pytest.mark.skipif(not (URL and S3), reason="needs GRADEMIND_TEST_DATABASE_URL and GRADEMIND_TEST_S3_ENDPOINT")
CORE = Path(__file__).resolve().parents[3] / "packages" / "core"


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


def new_booklet_job(db: sessionmaker[Session], store: ObjectStore, pages: int = 2) -> uuid.UUID:
    """A QUEUED ingest job over a fresh synthetic PDF booklet (as an upload would leave it)."""
    data = make_pdf([[f"booklet {uuid.uuid4().hex} page {n}"] for n in range(1, pages + 1)])
    return ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())


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
