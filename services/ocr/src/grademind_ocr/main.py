"""OCR service HTTP API: /ocr/page, /ocr/health, /ocr/version.

Startup loads the engine and runs the rule-12 assertion BEFORE the server accepts requests; on a mismatch the process
exits non-zero, so a substituted model can never serve a page. CPU inference is serialised (one page at a time) so the
container's memory stays bounded; callers waiting longer than `GRADEMIND_OCR_QUEUE_TIMEOUT_S` get 503 and retry later.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse

from grademind_ocr.rule12 import assert_resolved, load_expected

MAX_IMAGE_BYTES = 25 * 1024 * 1024
SERVICE_VERSION = "ocr-service-0.1.0"


class Engine(Protocol):
    def resolved(self) -> dict[str, Any]: ...
    def read(self, data: bytes) -> dict[str, Any]: ...


def _paddle_engine() -> Engine:
    from grademind_ocr.engine import PaddleEngine  # imported lazily: tests run without Paddle installed

    return PaddleEngine(load_expected())


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(engine_factory: Callable[[], Engine] = _paddle_engine, queue_timeout_s: float | None = None) -> FastAPI:
    expected = load_expected()
    timeout = queue_timeout_s if queue_timeout_s is not None else float(os.environ.get("GRADEMIND_OCR_QUEUE_TIMEOUT_S", "120"))
    lock = threading.Lock()
    state: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        t = time.perf_counter()
        engine = engine_factory()
        assert_resolved(expected, engine.resolved())  # raises Rule12Violation -> startup fails -> process exits
        state["engine"], state["load_s"] = engine, round(time.perf_counter() - t, 2)
        yield

    app = FastAPI(title="GradeMIND OCR", version=SERVICE_VERSION, lifespan=lifespan)

    @app.get("/ocr/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "rule12": "OK", "resolved": state["engine"].resolved(), "model_load_s": state["load_s"]}

    @app.get("/ocr/version")
    def version() -> dict[str, Any]:
        r = state["engine"].resolved()
        return {"service": SERVICE_VERSION, "libraries": r["libraries"], "models": {k: v["name"] for k, v in r["models"].items()}}

    @app.post("/ocr/page", response_model=None)
    def page(image: Annotated[UploadFile, File()]) -> dict[str, Any] | JSONResponse:
        data = image.file.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            return _err(413, "too_large", "The image is larger than 25 MB.")
        if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")):
            return _err(422, "unsupported_type", "Only PNG and JPEG page images are accepted.")
        if not lock.acquire(timeout=timeout):
            return _err(503, "busy", "The OCR service is busy. Try again shortly.")
        try:
            t = time.perf_counter()
            try:
                out = state["engine"].read(data)
            except ValueError as e:
                return _err(422, "undecodable", str(e))
            latency = round(time.perf_counter() - t, 3)
        finally:
            lock.release()
        r = state["engine"].resolved()
        return {**out, "latency_s": latency, "engine": {"libraries": r["libraries"], "models": r["models"]}}

    return app
