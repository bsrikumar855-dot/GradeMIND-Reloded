"""Pipeline definitions (spec §15, re-scoped by D19/D24/D28). ingest = INTAKE (storage integrity) -> RASTERIZE (page images
for the viewer; grading can start as soon as this stage is done) -> OCR (display-only machine reading, never needed for grading).
ocr_retry = OCR again for one submission (pages that failed or were never read)."""

from __future__ import annotations

import hashlib
import io

from sqlalchemy import select

from grademind_core.db.models import Page, Submission
from grademind_core.jobs import OCR_RETRY_KIND, Pipeline, Stage, StageContext, StageError
from grademind_core.pdf import PdfError, page_images
from grademind_core.storage import ObjectKind, ObjectStore
from grademind_worker.ocr_stage import OCR_VERSION, run_ocr

INGEST = "ingest"


def _submission(ctx: StageContext) -> Submission:
    with ctx.sessions() as s:
        sub = s.get(Submission, ctx.job.submission_id)
        if sub is None:
            raise StageError("missing_submission", "The submission for this job no longer exists.")
        s.expunge(sub)
        return sub


def _intake_input(ctx: StageContext) -> str:
    # per submission: stage outputs (page rows) belong to one submission, and the cache is global across jobs, so
    # the same file uploaded to two exams must not share a cached (skipped) stage
    sub = _submission(ctx)
    return f"{sub.id}:{sub.source_sha256}"


def _intake(ctx: StageContext) -> str | None:
    """Re-read the stored source and check it is byte-identical to what was uploaded (storage integrity)."""
    sub = _submission(ctx)
    store: ObjectStore = ctx.services["store"]
    if not store.exists(sub.source_object_key):
        raise StageError("source_missing", "The uploaded file is missing from storage. Upload it again.")
    if hashlib.sha256(store.get_bytes(sub.source_object_key)).hexdigest() != sub.source_sha256:
        raise StageError("source_corrupt", "The stored file does not match the upload. Upload it again.")
    return None


INTAKE = Stage(name="INTAKE", component_version="intake-0.1.0", input_hash=_intake_input, run=_intake)

RASTERIZE_VERSION = "rasterize-0.1.0"


def _rasterize(ctx: StageContext) -> str | None:
    """Booklet -> one JPEG + thumbnail per page. Resumable: pages already stored for this submission are kept, so a
    re-run after a crash only renders the missing ones (and never creates a duplicate page row)."""
    sub = _submission(ctx)
    store: ObjectStore = ctx.services["store"]
    with ctx.sessions() as s:
        done = set(s.scalars(select(Page.page_no).where(Page.submission_id == sub.id)))
    data = store.get_bytes(sub.source_object_key)
    n = 0
    try:
        for img in page_images(data, sub.source_mime):
            n += 1
            if img.page_no in done:
                continue
            key = store.put(ObjectKind.PAGE_IMAGE, io.BytesIO(img.jpeg), len(img.jpeg), "image/jpeg")
            thumb = store.put(ObjectKind.PAGE_THUMB, io.BytesIO(img.thumb_jpeg), len(img.thumb_jpeg), "image/jpeg")
            with ctx.sessions() as s, s.begin():
                s.add(
                    Page(
                        submission_id=sub.id,
                        page_no=img.page_no,
                        object_key=key,
                        thumb_object_key=thumb,
                        sha256=hashlib.sha256(img.jpeg).hexdigest(),
                        width=img.width,
                        height=img.height,
                        renderer=RASTERIZE_VERSION,
                    )
                )
    except PdfError as e:
        raise StageError("unreadable_booklet", str(e)) from e
    return f"pages:{n}"


RASTERIZE = Stage(name="RASTERIZE", component_version=RASTERIZE_VERSION, input_hash=_intake_input, run=_rasterize)

OCR = Stage(name="OCR", component_version=OCR_VERSION, input_hash=_intake_input, run=run_ocr)


def _reread_input(ctx: StageContext) -> str:
    # never cached: a re-read is an explicit request, so the job id is part of the input
    return f"{_intake_input(ctx)}:reread:{ctx.job.id}"


OCR_REREAD = Stage(name="OCR", component_version=OCR_VERSION, input_hash=_reread_input, run=run_ocr)

OCR_RETRY = OCR_RETRY_KIND
PIPELINES: dict[str, Pipeline] = {
    INGEST: Pipeline(kind=INGEST, stages=(INTAKE, RASTERIZE, OCR)),
    OCR_RETRY: Pipeline(kind=OCR_RETRY, stages=(OCR_REREAD,)),
}
