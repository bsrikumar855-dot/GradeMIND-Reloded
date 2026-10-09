"""Log redaction (D26.4). Secrets must never reach a log line, whatever logger produced it.

`install()` wraps the global LogRecord factory, so EVERY record (ours, uvicorn's, any library's) is redacted when it is
created, before any handler or formatter sees it, including formatted exception tracebacks. `redact_obj` is applied to
structured log fields by key. Idempotent; call it at process start (API app factory, worker).
"""

from __future__ import annotations

import logging
import re
import traceback
from typing import Any

MASK = "[REDACTED]"
SENSITIVE_KEY = re.compile(r"pass(word)?|passwd|secret|token|authorization|cookie|api[_-]?key|credential|signature", re.I)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 " + MASK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"), MASK),  # JWTs anywhere
    (
        re.compile(
            r"""(?i)(["']?(?:pass(?:word)?|passwd|secret|token|api[_-]?key|authorization|cookie)["']?\s*[:=]\s*)("[^"]*"|'[^']*'|[^\s,&;}"'\\]+)"""
        ),
        r"\1" + MASK,
    ),
    (re.compile(r"(?i)(X-Amz-(?:Signature|Credential|Security-Token)=)[^&\s\"']+"), r"\1" + MASK),
]


def redact_text(s: str) -> str:
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: (MASK if isinstance(k, str) and SENSITIVE_KEY.search(k) else redact_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


_installed = False


def install() -> None:
    global _installed
    if _installed:
        return
    base = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = base(*args, **kwargs)
        try:
            record.msg = redact_text(record.getMessage())
            record.args = None
            if record.exc_info:
                record.exc_text = redact_text("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
        except Exception:  # noqa: BLE001,S110 - a broken format string must not break logging
            pass
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
