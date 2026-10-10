"""Recording machine-read pages (D28). Display-only data: nothing in the grading path imports this module.

`config_hash` is the sha256 of the RESOLVED engine configuration read back from the running service (rule 12): library
versions, model names, weight-file hashes and pipeline settings. Two runs with the same hash used the same engine, and the
database allows only ONE successful run per (page, hash), so duplicate or concurrent runs cannot double-write.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from grademind_core.db.ocr_models import OcrLine, OcrRun
from grademind_core.ocr_client import OcrPageResult

STATUS_OK = "OK"
STATUS_FAILED = "FAILED"


def config_hash(resolved: dict[str, Any]) -> str:
    """Canonical (sorted, compact) JSON of the resolved configuration, hashed. Stable across key order."""
    canon = json.dumps(resolved, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canon.encode()).hexdigest()


def engine_signature(resolved: dict[str, Any]) -> dict[str, Any]:
    """The part of the resolved config that every /ocr/page response repeats (libraries + models): compared per page, so a
    service that was swapped mid-run cannot silently mix two engines under one config hash."""
    return {"libraries": resolved.get("libraries", {}), "models": resolved.get("models", {})}


def model_names(resolved: dict[str, Any]) -> dict[str, str]:
    return {role: str(m.get("name")) for role, m in resolved.get("models", {}).items()}


def weights(resolved: dict[str, Any]) -> dict[str, Any]:
    return {role: m.get("sha256", {}) for role, m in resolved.get("models", {}).items()}


def has_ok_run(db: Session, page_id: uuid.UUID, cfg: str) -> bool:
    return (
        db.scalar(select(OcrRun.id).where(OcrRun.page_id == page_id, OcrRun.config_hash == cfg, OcrRun.status == STATUS_OK))
        is not None
    )


def _base(page_id: uuid.UUID, provider: str, resolved: dict[str, Any], cfg: str, version: str, status: str) -> OcrRun:
    return OcrRun(
        page_id=page_id,
        provider=provider,
        status=status,
        config_hash=cfg,
        resolved=resolved,
        model_names=model_names(resolved),
        weights_sha256=weights(resolved),
        component_version=version,
    )


def record_ok(
    db: Session, page_id: uuid.UUID, provider: str, resolved: dict[str, Any], cfg: str, version: str, result: OcrPageResult
) -> bool:
    """Append a successful run and its lines in the caller's session. Returns False (and writes nothing) if a successful
    run for this page and engine config already exists, e.g. a concurrent worker got there first."""
    run = _base(page_id, provider, resolved, cfg, version, STATUS_OK)
    run.image_width, run.image_height, run.latency_s = result.width, result.height, result.latency_s
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    db.add_all(
        OcrLine(ocr_run_id=run.id, line_no=i, polygon=ln.poly, bbox=ln.box, text=ln.text, score=ln.score)
        for i, ln in enumerate(result.lines)
    )
    db.commit()
    return True


def record_failed(
    db: Session, page_id: uuid.UUID, provider: str, resolved: dict[str, Any], cfg: str, version: str, code: str, message: str
) -> None:
    run = _base(page_id, provider, resolved, cfg, version, STATUS_FAILED)
    run.error = f"{code}: {message}"
    db.add(run)
    db.commit()
