"""A stand-in for the OCR service client (the real engine is exercised in compose-smoke and the browser E2E).

Same interface as grademind_core.ocr_client.OcrServiceClient; registered through the real ProviderRegistry, so the stage under
test goes through exactly the code path production uses. Stdlib only (no PIL in the worker's import graph).
"""

from __future__ import annotations

import copy
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

from grademind_core.config import Env, OcrProvider, Settings
from grademind_core.ocr_client import OcrLineOut, OcrPageError, OcrPageResult, OcrUnavailableError
from grademind_core.providers import ProviderRegistry

RESOLVED: dict[str, Any] = {
    "libraries": {"paddlepaddle": "3.4.0", "paddleocr": "3.7.0"},
    "models": {
        "det": {"name": "PP-OCRv6_medium_det", "sha256": {"inference.pdiparams": "d" * 64}},
        "rec": {"name": "PP-OCRv6_medium_rec", "sha256": {"inference.pdiparams": "r" * 64}},
    },
    "pipeline": {"device": "cpu", "text_det_limit_side_len": 1920},
}


def jpeg_size(data: bytes) -> tuple[int, int]:
    """(width, height) from the first SOF marker of a JPEG."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", data[i + 5 : i + 9])
            return w, h
        i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
    raise ValueError("no SOF marker")


def default_lines(call_no: int, width: int, height: int) -> list[OcrLineOut]:
    def box(x0: float, y0: float, x1: float, y1: float) -> list[int]:
        return [int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)]

    def poly(b: list[int]) -> list[list[int]]:
        return [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]

    a, b = box(0.1, 0.10, 0.9, 0.13), box(0.1, 0.20, 0.8, 0.23)
    return [
        OcrLineOut(f"page {call_no} first line", 0.93, poly(a), a),
        OcrLineOut(f"page {call_no} second line", 0.41, poly(b), b),
    ]


class FakeOcr:
    def __init__(
        self,
        lines: Callable[[int, int, int], list[OcrLineOut]] = default_lines,
        *,
        fail_calls: set[int] | None = None,  # 1-based read_page call numbers that raise OcrPageError
        down_from: int | None = None,  # read_page calls >= this raise OcrUnavailableError (service died)
        health_down: bool = False,
        delay_s: float = 0.0,
        engine: dict[str, Any] | None = None,
        resolved: dict[str, Any] | None = None,
        size_override: tuple[int, int] | None = None,
    ) -> None:
        self.lines, self.fail_calls, self.down_from, self.health_down, self.delay_s = (
            lines,
            fail_calls or set(),
            down_from,
            health_down,
            delay_s,
        )
        self.resolved = copy.deepcopy(resolved or RESOLVED)
        self.engine = engine
        self.size_override = size_override
        self.calls = 0
        self._lock = threading.Lock()

    def health(self) -> dict[str, Any]:
        if self.health_down:
            raise OcrUnavailableError("OCR service unreachable: ConnectError")
        return {"status": "ok", "rule12": "OK", "resolved": copy.deepcopy(self.resolved)}

    def read_page(self, image: bytes) -> OcrPageResult:
        with self._lock:
            self.calls += 1
            n = self.calls
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.down_from is not None and n >= self.down_from:
            raise OcrUnavailableError("OCR service unreachable: ConnectError")
        if n in self.fail_calls:
            raise OcrPageError("undecodable", "The image could not be decoded.")
        w, h = self.size_override or jpeg_size(image)
        engine = self.engine or {"libraries": self.resolved["libraries"], "models": self.resolved["models"]}
        return OcrPageResult(width=w, height=h, lines=self.lines(n, w, h), latency_s=0.01, engine=copy.deepcopy(engine))


def registry(fake: FakeOcr | None, *, enabled: bool = True) -> ProviderRegistry:
    """The real ProviderRegistry. `enabled=False` disables the OCR provider through the single config source."""
    settings = Settings(env=Env.TEST, ocr_providers_enabled=[OcrProvider.PADDLE_V6] if enabled else [])
    reg = ProviderRegistry(settings)
    if fake is not None:
        reg.register_ocr(OcrProvider.PADDLE_V6, lambda: fake)
    return reg


def services(store: Any, fake: FakeOcr | None = None, *, enabled: bool = True, abort_after: int = 2) -> dict[str, Any]:
    return {
        "store": store,
        "providers": registry(fake if fake is not None else FakeOcr(), enabled=enabled),
        "ocr_abort_after_unavailable": abort_after,
    }
