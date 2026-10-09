"""BodySizeLimit at the ASGI level: proves the body is cut off as it streams in, not after it was fully received."""

from __future__ import annotations

import json
from typing import Any

from grademind_api.limits import BodySizeLimit

CHUNK = 64 * 1024
LIMIT = 256 * 1024


async def drive(headers: list[tuple[bytes, bytes]], chunks: int) -> tuple[list[dict[str, Any]], int, bool]:
    pulled = 0
    app_called = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal pulled
        pulled += 1
        return {"type": "http.request", "body": b"0" * CHUNK, "more_body": pulled < chunks}

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    async def app(scope: Any, rcv: Any, snd: Any) -> None:  # reads the whole body, like a multipart parser
        nonlocal app_called
        app_called = True
        while (await rcv()).get("more_body"):
            pass
        await snd({"type": "http.response.start", "status": 200, "headers": []})
        await snd({"type": "http.response.body", "body": b"ok"})

    scope = {"type": "http", "headers": headers, "state": {"request_id": "rid-1"}}
    await BodySizeLimit(app, LIMIT)(scope, receive, send)  # type: ignore[arg-type]
    return sent, pulled, app_called


async def test_streamed_body_is_cut_off_at_the_limit() -> None:
    sent, pulled, _ = await drive([(b"transfer-encoding", b"chunked")], chunks=64)
    assert sent[0]["status"] == 413
    assert pulled == LIMIT // CHUNK + 1  # stopped on the first chunk past the limit, not after 64
    err = json.loads(sent[1]["body"])["error"]
    assert err["code"] == "too_large" and err["request_id"] == "rid-1"


async def test_declared_oversize_is_rejected_before_reading() -> None:
    sent, pulled, app_called = await drive([(b"content-length", str(LIMIT + 1).encode())], chunks=64)
    assert sent[0]["status"] == 413 and pulled == 0 and not app_called


async def test_body_within_limit_passes_through() -> None:
    sent, pulled, _ = await drive([(b"content-length", str(2 * CHUNK).encode())], chunks=2)
    assert sent[0]["status"] == 200 and pulled == 2
