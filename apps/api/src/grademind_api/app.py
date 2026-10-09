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
from grademind_api.limits import BodySizeLimit
from grademind_api.queue import CeleryQueue, JobQueue
from grademind_api.routes import auth, exams, health, jobs, submissions
from grademind_api.routes.submissions import MULTIPART_OVERHEAD
from grademind_core.config import Settings, get_settings
from grademind_core.db.session import session_factory
from grademind_core.logredact import install as install_log_redaction
from grademind_core.logredact import redact_obj
from grademind_core.storage import ObjectStore

log = logging.getLogger("grademind.api")


def _json_log(**fields: object) -> None:
    log.info(json.dumps(redact_obj(fields), default=str))  # spec §18: structured JSON logs; D26.4: redacted


def create_app(
    settings: Settings | None = None,
    sessions: sessionmaker[Session] | None = None,
    store: ObjectStore | None = None,
    queue: JobQueue | None = None,
) -> FastAPI:
    install_log_redaction()  # D26.4: every log record in this process is redacted at creation
    settings = settings or get_settings()
    app = FastAPI(title="GradeMIND API", version="0.1.0")
    app.state.settings = settings
    app.state.session_factory = sessions or session_factory(settings.database_url)
    app.state.store = store or ObjectStore(settings)  # constructing the client makes no network call
    app.state.queue = queue or CeleryQueue(settings.redis_url)
    # added first, so it runs inside the request-id middleware and its 413s are logged with a request_id
    app.add_middleware(BodySizeLimit, max_body_bytes=settings.max_upload_bytes + MULTIPART_OVERHEAD)

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
        # never log e.errors() whole: pydantic puts the offending input there (e.g. a login body with its password)
        detail = [{"loc": err.get("loc"), "type": err.get("type"), "msg": err.get("msg")} for err in e.errors()]
        _json_log(request_id=request.state.request_id, error="validation", detail=detail)
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
    app.include_router(submissions.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    return app
