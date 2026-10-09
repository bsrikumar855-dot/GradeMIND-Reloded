"""Pipeline definitions. Phase 1 has one real stage, INTAKE; Phase 2 adds PREPROCESSING, OCR, ... (spec §15, re-scoped by D19)."""

from __future__ import annotations

import hashlib

from grademind_core.db.models import Submission
from grademind_core.jobs import Pipeline, Stage, StageContext, StageError
from grademind_core.storage import ObjectStore

INGEST = "ingest"


def _submission(ctx: StageContext) -> Submission:
    with ctx.sessions() as s:
        sub = s.get(Submission, ctx.job.submission_id)
        if sub is None:
            raise StageError("missing_submission", "The submission for this job no longer exists.")
        s.expunge(sub)
        return sub


def _intake_input(ctx: StageContext) -> str:
    return _submission(ctx).source_sha256


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

PIPELINES: dict[str, Pipeline] = {INGEST: Pipeline(kind=INGEST, stages=(INTAKE,))}
