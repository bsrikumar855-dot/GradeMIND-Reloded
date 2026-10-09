"""INTAKE stage against real Postgres + MinIO: intact source passes; a missing or altered object fails with a safe reason."""

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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from grademind_core.config import Env, Settings
from grademind_core.db.models import Exam, JobStatus, Organization, ProcessingJob, Role, Submission, User
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


def ingest_job(db: sessionmaker[Session], key: str, sha: str) -> uuid.UUID:
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
            source_mime="application/pdf",
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


def status_and_error(db: sessionmaker[Session], jid: uuid.UUID) -> tuple[JobStatus, str | None]:
    with db() as s:
        j = s.get(ProcessingJob, jid)
        assert j is not None
        return j.status, j.error


def test_intact_source_passes(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = b"%PDF-1.7 intact " + uuid.uuid4().bytes
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    assert run_job(db, jid, PIPELINES, services={"store": store}) == JobStatus.COMPLETED


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
