"""Consistent error envelope (spec §15/§17): humans get a message, logs keep the technical detail with the request_id.
`issues` (optional) lists every problem of a submitted document, each with a path, so an editor can show them all."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, issues: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.issues = status, code, message, issues


def envelope(code: str, message: str, request_id: str, issues: list[dict[str, str]] | None = None) -> dict[str, dict[str, Any]]:
    err: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if issues is not None:
        err["issues"] = issues
    return {"error": err}
