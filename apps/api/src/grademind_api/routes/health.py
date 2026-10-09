"""Health endpoints (spec §15). Each dependency is probed independently; a failure is reported, never hidden."""

from __future__ import annotations

from typing import Any

import httpx
import redis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from grademind_api.deps import db_dep, settings_dep, store_dep
from grademind_core.config import Settings
from grademind_core.storage import ObjectStore

router = APIRouter()


def _resp(ok: bool, **detail: Any) -> JSONResponse:
    return JSONResponse({"status": "ok" if ok else "unavailable", **detail}, status_code=200 if ok else 503)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/db")
def health_db(db: Session = Depends(db_dep)) -> JSONResponse:
    try:
        rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        return _resp(True, alembic_revision=rev)
    except Exception as e:  # noqa: BLE001 - reported to the caller as 503
        return _resp(False, error=type(e).__name__)


@router.get("/health/redis")
def health_redis(settings: Settings = Depends(settings_dep)) -> JSONResponse:
    try:
        ok = bool(redis.Redis.from_url(settings.redis_url, socket_timeout=2).ping())
        return _resp(ok)
    except Exception as e:  # noqa: BLE001
        return _resp(False, error=type(e).__name__)


@router.get("/health/storage")
def health_storage(store: ObjectStore = Depends(store_dep)) -> JSONResponse:
    try:
        return _resp(store.health())
    except Exception as e:  # noqa: BLE001
        return _resp(False, error=type(e).__name__)


@router.get("/health/ocr")
def health_ocr(settings: Settings = Depends(settings_dep)) -> JSONResponse:
    """Proxies the OCR service's own health, which reports the RESOLVED models and weight hashes (rule 12)."""
    try:
        r = httpx.get(f"{settings.ocr_service_url}/ocr/health", timeout=3)
        return _resp(r.status_code == 200, ocr=r.json())
    except Exception as e:  # noqa: BLE001
        return _resp(False, error=type(e).__name__)


@router.get("/health/models")
def health_models(settings: Settings = Depends(settings_dep)) -> dict[str, Any]:
    """D19: v1 runs no LLM. Reports the flag state from the single config source."""
    return {
        "status": "ok",
        "ai_suggestions_enabled": settings.ai_suggestions_enabled,
        "llm_kill_switch": settings.llm_kill_switch,
        "llm_providers_enabled": [p.value for p in settings.llm_providers_enabled],
        "ocr_providers_enabled": [p.value for p in settings.ocr_providers_enabled],
    }
