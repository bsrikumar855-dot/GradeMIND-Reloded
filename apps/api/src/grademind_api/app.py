"""FastAPI application factory. No pipeline work runs inside a request (spec §4): uploads enqueue jobs."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from grademind_api.errors import ApiError, envelope
from grademind_api.routes import auth, exams, health
from grademind_core.config import Settings, get_settings
from grademind_core.db.session import session_factory

log = logging.getLogger("grademind.api")


def _json_log(**fields: object) -> None:
    log.info(json.dumps(fields, default=str))  # spec §18: structured JSON logs


def create_app(settings: Settings | None = None, sessions: sessionmaker[Session] | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="GradeMIND API", version="0.1.0")
    app.state.settings = settings
    app.state.session_factory = sessions or session_factory(settings.database_url)

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = rid
        t = time.perf_counter()
        resp = await call_next(request)
        resp.headers["x-request-id"] = rid
        _json_log(
            request_id=rid,
            method=request.method,
            path=request.url.path,
            status=resp.status_code,
            latency_ms=round((time.perf_counter() - t) * 1000, 1),
        )
        return resp

    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError) -> JSONResponse:
        return JSONResponse(envelope(e.code, e.message, request.state.request_id), status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, e: RequestValidationError) -> JSONResponse:
        _json_log(request_id=request.state.request_id, error="validation", detail=e.errors())
        return JSONResponse(
            envelope("invalid_request", "Some fields are missing or invalid.", request.state.request_id), status_code=422
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, e: Exception) -> JSONResponse:
        _json_log(request_id=getattr(request.state, "request_id", None), error=type(e).__name__, detail=str(e))
        rid = getattr(request.state, "request_id", "")
        return JSONResponse(envelope("internal_error", "Something went wrong. Please try again.", rid), status_code=500)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api")
    app.include_router(exams.router, prefix="/api")
    return app
