"""Request body size limit as a pure ASGI middleware (spec §16 upload size limits).

Starlette spools an entire multipart body to disk before an endpoint runs, so an endpoint-level check alone would let a
client fill the disk. This middleware rejects an oversized declared Content-Length up front and counts the bytes of
bodies without one (chunked), cutting the request off as soon as the limit is crossed.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from grademind_api.errors import envelope


class _TooLargeError(Exception):
    pass


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max = max_body_bytes

    async def _reject(self, scope: Scope, send: Send) -> None:
        rid = _request_id(scope)
        body = json.dumps(
            envelope("too_large", f"The upload is larger than the {self.max // (1024 * 1024)} MB limit.", rid)
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"x-request-id", rid.encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max:
            await self._reject(scope, send)
            return

        seen = 0
        exceeded = False
        started = False

        async def counting_receive() -> Message:
            nonlocal seen, exceeded
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    exceeded = True
                    raise _TooLargeError
            return msg

        async def guarded_send(msg: Message) -> None:
            nonlocal started
            if exceeded:
                return  # whatever the app tried to say about the aborted body, the answer is 413
            started = True
            await send(msg)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:  # frameworks may wrap our signal (e.g. "error parsing the body"); anything else propagates
                raise
        if exceeded and not started:
            await self._reject(scope, send)


def _request_id(scope: Scope) -> str:
    state: dict[str, Any] = scope.get("state") or {}
    rid = state.get("request_id")
    if isinstance(rid, str):
        return rid
    for k, v in scope.get("headers") or []:
        if k == b"x-request-id":
            return str(v.decode("latin-1"))
    return uuid.uuid4().hex
