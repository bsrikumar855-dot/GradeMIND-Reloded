"""ingest pipeline (INTAKE -> RASTERIZE) against real Postgres + MinIO."""

from __future__ import annotations

import hashlib
import io
import uuid

from fake_ocr import services
from pdfgen import make_pdf, make_png
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from worker_world import ingest_job, needs_stack, pages_of, put, status_and_error

from grademind_core.db.models import JobStatus, Page, ProcessingJob
from grademind_core.db.ocr_models import OcrRun
from grademind_core.jobs import run_job
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_worker.stages import PIPELINES

pytest_plugins = ["worker_world"]
pytestmark = needs_stack


def test_pdf_booklet_becomes_page_images(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = make_pdf([["page one " + uuid.uuid4().hex], ["page two"], ["page three"]])
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.COMPLETED
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
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.COMPLETED
    assert [(pg.page_no, pg.width, pg.height) for pg in pages_of(db, jid)] == [(1, 800, 1100)]


def test_unreadable_pdf_fails_rasterize_with_a_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    data = b"%PDF-1.7 this is not really a pdf " + uuid.uuid4().bytes
    jid = ingest_job(db, put(store, data), hashlib.sha256(data).hexdigest())
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.FAILED
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
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.COMPLETED
    pages = pages_of(db, jid)
    assert (
        [pg.page_no for pg in pages] == [1, 2] and pages[0].width == 1 and pages[1].width == 1240
    )  # page 1 kept, page 2 rendered
    # the stub page 1 has no stored image: that page is recorded as unreadable, page 2 is read, the job still completes
    with db() as s:
        runs = {r.page_id: r for r in s.scalars(select(OcrRun))}
    assert runs[pages[0].id].status == "FAILED" and runs[pages[0].id].error.startswith("image_missing:")
    assert runs[pages[1].id].status == "OK"


def test_same_file_in_two_exams_gets_pages_in_both(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    """Regression: stage caching is global, so the input hash must include the submission."""
    db, store = env
    data = make_pdf([["shared " + uuid.uuid4().hex]])
    sha = hashlib.sha256(data).hexdigest()
    j1, j2 = ingest_job(db, put(store, data), sha), ingest_job(db, put(store, data), sha)
    for j in (j1, j2):
        assert run_job(db, j, PIPELINES, services=services(store)) == JobStatus.COMPLETED
    assert len(pages_of(db, j1)) == 1 and len(pages_of(db, j2)) == 1


def test_altered_source_fails_with_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = ingest_job(db, put(store, b"%PDF-1.7 altered"), "0" * 64)
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.FAILED
    assert status_and_error(db, jid) == (
        JobStatus.FAILED,
        "INTAKE_FAILED: The stored file does not match the upload. Upload it again.",
    )


def test_missing_source_fails_with_safe_reason(env: tuple[sessionmaker[Session], ObjectStore]) -> None:
    db, store = env
    jid = ingest_job(db, "submission-source/" + uuid.uuid4().hex, "1" * 64)
    assert run_job(db, jid, PIPELINES, services=services(store)) == JobStatus.FAILED
    assert status_and_error(db, jid)[1] == "INTAKE_FAILED: The uploaded file is missing from storage. Upload it again."
