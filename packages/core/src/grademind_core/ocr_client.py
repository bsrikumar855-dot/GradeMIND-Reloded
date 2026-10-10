"""Client for the local OCR service (services/ocr). The ONLY way application code talks to it, and only ever obtained through
the provider registry (rule 7): `registry.ocr(OcrProvider.PADDLE_V6)`.

Machine-read text is display-only data (D28): nothing in the grading path may import this module (import-linter contract).
Errors are split in two on purpose: a PAGE error (this page cannot be read, e.g. it does not decode) is recorded and the run
moves on; an UNAVAILABLE error (the service is down, busy or broken) is systemic and the caller backs off.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx


class OcrUnavailableError(Exception):
    """The service cannot be reached, is busy, or failed its own model check (rule 12). Safe to retry later."""


class OcrPageError(Exception):
    """This page cannot be read (undecodable, too large, wrong type). `code` is a stable reason; `message` is safe to show."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class OcrLineOut:
    text: str
    score: float | None
    poly: list[list[int]]
    box: list[int]


@dataclass(frozen=True)
class OcrPageResult:
    width: int
    height: int
    lines: list[OcrLineOut]
    latency_s: float
    engine: dict[str, Any]  # libraries + models (with weight hashes) that PRODUCED this result (rule 12)


HEALTH_TIMEOUT_S = 30.0
HEALTH_ATTEMPTS = 3


class OcrServiceClient:
    def __init__(
        self,
        base_url: str,
        timeout_s: float = 180.0,
        busy_retries: int = 3,
        busy_wait_s: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout_s, connect=5.0))
        self._busy_retries, self._busy_wait_s, self._sleep = busy_retries, busy_wait_s, sleep

    def close(self) -> None:
        self._http.close()

    def health(self) -> dict[str, Any]:
        """The service's /ocr/health: {"status", "rule12", "resolved": {libraries, models, pipeline}, ...}. Raises
        OcrUnavailableError unless the service says it is healthy AND passed its rule-12 assertion."""
        # A service busy reading other booklets answers slowly (CPU-bound, one page at a time): a single short timeout would
        # fail the whole stage for a page it never tried. Retry a few times with a generous timeout before giving up.
        r: httpx.Response | None = None
        for attempt in range(HEALTH_ATTEMPTS):
            try:
                r = self._http.get("/ocr/health", timeout=HEALTH_TIMEOUT_S)
                break
            except httpx.HTTPError as e:
                if attempt == HEALTH_ATTEMPTS - 1:
                    raise OcrUnavailableError(f"OCR service unreachable: {type(e).__name__}") from e
                self._sleep(self._busy_wait_s)
        assert r is not None  # the loop either breaks with a response or raises
        if r.status_code != 200:
            raise OcrUnavailableError(f"OCR service health returned {r.status_code}")
        body: dict[str, Any] = r.json()
        if body.get("status") != "ok" or body.get("rule12") != "OK" or "resolved" not in body:
            raise OcrUnavailableError("OCR service did not report a verified model configuration")
        return body

    def read_page(self, image: bytes) -> OcrPageResult:
        for attempt in range(self._busy_retries + 1):
            try:
                r = self._http.post("/ocr/page", files={"image": ("page.jpg", image, "image/jpeg")})
            except httpx.HTTPError as e:
                raise OcrUnavailableError(f"OCR service unreachable: {type(e).__name__}") from e
            if r.status_code == 200:
                d = r.json()
                return OcrPageResult(
                    width=int(d["width"]),
                    height=int(d["height"]),
                    lines=[OcrLineOut(ln["text"], ln.get("score"), ln["poly"], ln["box"]) for ln in d["lines"]],
                    latency_s=float(d.get("latency_s", 0.0)),
                    engine=d["engine"],
                )
            if r.status_code == 503 and attempt < self._busy_retries:
                self._sleep(self._busy_wait_s)  # the service reads one page at a time; wait our turn
                continue
            if r.status_code in (413, 422):
                err = (r.json() or {}).get("error", {})
                raise OcrPageError(str(err.get("code", "unreadable")), str(err.get("message", "This page could not be read.")))
            raise OcrUnavailableError(f"OCR service returned {r.status_code}")
        raise OcrUnavailableError("OCR service stayed busy")  # pragma: no cover - the loop always returns or raises
