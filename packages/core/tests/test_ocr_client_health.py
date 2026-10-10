"""The OCR client's health call must survive a service that is busy reading other booklets (it answers slowly)."""

from __future__ import annotations

import httpx
import pytest

from grademind_core.ocr_client import HEALTH_ATTEMPTS, OcrServiceClient, OcrUnavailableError

OK = {"status": "ok", "rule12": "OK", "resolved": {"libraries": {}, "models": {}, "pipeline": {}}}


def _client(handler: httpx.MockTransport, sleeps: list[float]) -> OcrServiceClient:
    c = OcrServiceClient("http://ocr", sleep=sleeps.append)
    c._http = httpx.Client(base_url="http://ocr", transport=handler)  # noqa: SLF001 - swap the transport, keep the logic
    return c


def test_health_retries_after_a_timeout_then_succeeds() -> None:
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.extensions["timeout"]["read"])
        if len(calls) < 3:
            raise httpx.ReadTimeout("busy", request=request)
        return httpx.Response(200, json=OK)

    sleeps: list[float] = []
    assert _client(httpx.MockTransport(handler), sleeps).health()["status"] == "ok"
    assert len(calls) == 3 and sleeps == [5.0, 5.0]
    assert min(calls) >= 30.0  # a generous timeout: a busy service is slow, not down


def test_health_gives_up_after_the_attempts_and_says_unavailable() -> None:
    n = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(OcrUnavailableError, match="unreachable"):
        _client(httpx.MockTransport(handler), []).health()
    assert n == HEALTH_ATTEMPTS


def test_health_does_not_retry_a_definite_answer() -> None:
    n = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(503)

    with pytest.raises(OcrUnavailableError, match="503"):
        _client(httpx.MockTransport(handler), []).health()
    assert n == 1
