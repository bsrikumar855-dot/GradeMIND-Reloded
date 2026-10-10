"""OCR stage (3.1; D28): machine-read every page so the examiner can read faster. Display-only: the output is rows in
ocr_runs / ocr_lines and nothing else; no grading code reads them (import-linter contract).

Rules this stage keeps:
- OCR calls only through the provider registry (rule 7). A disabled provider means no call at all.
- Rule 12: the RESOLVED engine configuration is read back from the running service, hashed, stored on every run and logged
  as one structured line per stage run (detector, recogniser, weight hashes, library versions, pipeline settings).
- A page that cannot be read is recorded as a FAILED run and the stage moves on: a bad page never fails the job, and the page
  simply has no machine text. Only a SYSTEMIC problem (the service is down or busy for several pages in a row, or it fails its
  model check) fails the stage; pages already read stay stored, and a retry re-runs only the missing ones.
- Idempotent: a page that already has a successful run for this engine configuration is skipped, and the database allows only
  one such run, so concurrent or repeated runs cannot double-write.
- The stage never modifies a page image (D19): it only reads it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from grademind_core.config import OcrProvider
from grademind_core.db.models import Page
from grademind_core.jobs import StageContext, StageError
from grademind_core.ocr_client import OcrPageError, OcrServiceClient, OcrUnavailableError
from grademind_core.ocr_runs import config_hash, engine_signature, has_ok_run, record_failed, record_ok
from grademind_core.providers import ProviderDisabledError, ProviderRegistry
from grademind_core.storage import ObjectNotFoundError, ObjectStore

OCR_VERSION = "ocr-stage-0.1.0"
PROVIDER = OcrProvider.PADDLE_V6.value
log = logging.getLogger("grademind.worker")


@dataclass(frozen=True)
class _PageRef:
    id: uuid.UUID
    page_no: int
    object_key: str
    width: int
    height: int


def _pages(ctx: StageContext) -> list[_PageRef]:
    with ctx.sessions() as s:
        rows = s.scalars(select(Page).where(Page.submission_id == ctx.job.submission_id).order_by(Page.page_no))
        return [_PageRef(p.id, p.page_no, p.object_key, p.width, p.height) for p in rows]


def run_ocr(ctx: StageContext) -> str | None:
    registry: ProviderRegistry = ctx.services["providers"]
    store: ObjectStore = ctx.services["store"]
    abort_after: int = int(ctx.services.get("ocr_abort_after_unavailable", 2))
    try:
        client: OcrServiceClient = registry.ocr(OcrProvider.PADDLE_V6)
    except ProviderDisabledError:
        return "ocr:disabled"

    try:
        health = client.health()
    except OcrUnavailableError as e:
        raise StageError(
            "ocr_unavailable", "The text-reading service is not available right now. Grading is not affected; retry later."
        ) from e
    resolved = health["resolved"]
    cfg = config_hash(resolved)
    expected = engine_signature(resolved)
    pages = _pages(ctx)
    # rule 12: one structured line per stage run with what the service ACTUALLY loaded (read back, not requested)
    log.info(
        json.dumps(
            {
                "event": "ocr.run",
                "job_id": str(ctx.job.id),
                "config_hash": cfg,
                "libraries": resolved.get("libraries"),
                "models": {r: {"name": m.get("name"), "sha256": m.get("sha256")} for r, m in resolved.get("models", {}).items()},
                "pipeline": resolved.get("pipeline"),
                "pages": len(pages),
            }
        )
    )

    ok = failed = skipped = unread = consecutive = 0
    for pg in pages:
        with ctx.sessions() as s:
            if has_ok_run(s, pg.id, cfg):
                skipped += 1
                continue
        try:
            result = client.read_page(store.get_bytes(pg.object_key))
            consecutive = 0
        except ObjectNotFoundError:  # this page's image is gone: that page has no machine text, the others carry on
            with ctx.sessions() as s:
                record_failed(
                    s, pg.id, PROVIDER, resolved, cfg, OCR_VERSION, "image_missing", "The page image is missing from storage."
                )
            failed += 1
            continue
        except OcrPageError as e:
            with ctx.sessions() as s:
                record_failed(s, pg.id, PROVIDER, resolved, cfg, OCR_VERSION, e.code, e.message)
            failed += 1
            continue
        except OcrUnavailableError as e:
            unread += 1
            consecutive += 1
            log.warning(
                json.dumps({"event": "ocr.page_unavailable", "job_id": str(ctx.job.id), "page_no": pg.page_no, "error": str(e)})
            )
            if consecutive >= abort_after:
                break
            continue

        problem: tuple[str, str] | None = None
        if result.engine != expected:
            problem = ("engine_changed", "The reading service changed its models while this booklet was being read.")
        elif (result.width, result.height) != (pg.width, pg.height):
            problem = ("size_mismatch", "The machine reading used a different image size than the page.")
        with ctx.sessions() as s:
            if problem:
                record_failed(s, pg.id, PROVIDER, resolved, cfg, OCR_VERSION, *problem)
                failed += 1
            elif record_ok(s, pg.id, PROVIDER, resolved, cfg, OCR_VERSION, result):
                ok += 1
            else:
                skipped += 1  # a concurrent run stored this page first

    if unread:
        raise StageError(
            "ocr_incomplete",
            f"{unread} page(s) were not read because the text-reading service was unavailable. "
            "Grading is not affected; retry later.",
        )
    return f"ocr:ok={ok},failed={failed},skipped={skipped}"
