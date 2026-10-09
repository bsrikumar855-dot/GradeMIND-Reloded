"""OCR service without Paddle: the rule-12 startup assertion and the HTTP contract, using a fake engine.
The real engine is exercised in the container (`docker compose` smoke, Phase 1 step 1.7)."""

from __future__ import annotations

import copy
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from grademind_ocr.main import create_app
from grademind_ocr.rule12 import Rule12Violation, assert_resolved, load_expected, problems

EXPECTED = load_expected()
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def resolved_ok() -> dict[str, Any]:
    return {
        "libraries": dict(EXPECTED["libraries"]),
        "models": {k: {"name": v["name"], "sha256": dict(v["sha256"])} for k, v in EXPECTED["models"].items()},
        "pipeline": dict(EXPECTED["pipeline_resolved"]),
    }


class FakeEngine:
    def __init__(self, resolved: dict[str, Any], delay: float = 0.0) -> None:
        self._r, self.delay, self.calls = resolved, delay, 0

    def resolved(self) -> dict[str, Any]:
        return self._r

    def read(self, data: bytes) -> dict[str, Any]:
        self.calls += 1
        time.sleep(self.delay)
        return {"width": 10, "height": 10, "lines": [{"text": "hello", "score": 0.9, "poly": [], "box": [0, 0, 1, 1]}]}


def test_expected_manifest_pins_d18_models() -> None:
    assert EXPECTED["models"]["det"]["name"] == "PP-OCRv6_medium_det"
    assert EXPECTED["models"]["rec"]["name"] == "PP-OCRv6_medium_rec"
    assert EXPECTED["pipeline_args"]["device"] == "cpu" and EXPECTED["pipeline_resolved"]["device"] == "cpu"
    assert all(len(d) == 64 for m in EXPECTED["models"].values() for d in m["sha256"].values())


def test_matching_resolution_passes() -> None:
    assert problems(EXPECTED, resolved_ok()) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["models"]["det"].update(name="PP-OCRv5_server_det"),  # the 3.7 silent substitution (rule 12 origin)
        lambda r: r["models"]["rec"]["sha256"].update({"inference.pdiparams": "0" * 64}),  # same name, different weights
        lambda r: r["models"]["rec"]["sha256"].update({"inference.yml": None}),  # weight file missing
        lambda r: r["libraries"].update(paddleocr="3.8.0"),
        lambda r: r["pipeline"].update(use_textline_orientation=True),
        lambda r: r["pipeline"].update(text_det_box_thresh=0.5),  # a threshold change alters output silently
        lambda r: r["pipeline"].pop("text_det_limit_side_len"),  # not readable = not verified = refused
    ],
)
def test_any_difference_is_a_violation(mutate: Any) -> None:
    r = copy.deepcopy(resolved_ok())
    mutate(r)
    with pytest.raises(Rule12Violation, match="RULE 12 VIOLATION"):
        assert_resolved(EXPECTED, r)


def test_service_refuses_to_start_on_mismatch() -> None:
    bad = copy.deepcopy(resolved_ok())
    bad["models"]["det"]["name"] = "PP-OCRv6_medium_det_substituted"
    with pytest.raises(Rule12Violation), TestClient(create_app(lambda: FakeEngine(bad))):
        pass


def test_health_version_and_page() -> None:
    eng = FakeEngine(resolved_ok())
    with TestClient(create_app(lambda: eng)) as c:
        h = c.get("/ocr/health").json()
        assert h["status"] == "ok" and h["rule12"] == "OK" and h["resolved"]["models"]["rec"]["name"] == "PP-OCRv6_medium_rec"
        assert c.get("/ocr/version").json()["models"] == {"det": "PP-OCRv6_medium_det", "rec": "PP-OCRv6_medium_rec"}
        r = c.post("/ocr/page", files={"image": ("p.png", PNG, "image/png")})
        assert r.status_code == 200 and r.json()["lines"][0]["text"] == "hello"
        assert r.json()["engine"]["models"]["det"]["name"] == "PP-OCRv6_medium_det"  # every result carries what produced it
        assert c.post("/ocr/page", files={"image": ("p.pdf", b"%PDF-1.7", "application/pdf")}).status_code == 422
        assert eng.calls == 1


def test_inference_is_serialised_and_busy_returns_503() -> None:
    eng = FakeEngine(resolved_ok(), delay=0.5)
    with TestClient(create_app(lambda: eng, queue_timeout_s=0.1)) as c:
        codes: list[int] = []
        ts = [
            threading.Thread(target=lambda: codes.append(c.post("/ocr/page", files={"image": ("p.png", PNG)}).status_code))
            for _ in range(2)
        ]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    assert sorted(codes) == [200, 503] and eng.calls == 1
