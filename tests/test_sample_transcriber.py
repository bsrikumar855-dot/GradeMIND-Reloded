"""D31.2: the local transcription tool for the pilot sample: blind (no machine text), loopback only, whitelisted routes."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sample_transcriber", ROOT / "scripts" / "sample_transcriber.py")
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

MACHINE_TEXT = "ZEBRAQUARTZ machine reading"  # must never be sent to the browser


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    (tmp_path / "crops").mkdir()
    for sid in ("s001", "s002"):
        (tmp_path / "crops" / f"{sid}.jpg").write_bytes(b"\xff\xd8\xff-" + sid.encode())
    (tmp_path / "machine.json").write_text(json.dumps({"s001": {"text": MACHINE_TEXT}, "s002": {"text": MACHINE_TEXT}}))
    (tmp_path / "transcriptions.json").write_text(
        json.dumps(
            {"batch": "B", "lines": {"s001": {"text": None, "unreadable": False}, "s002": {"text": None, "unreadable": False}}}
        )
    )
    srv = ThreadingHTTPServer(("127.0.0.1", 0), mod.make_handler(tmp_path))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path
    srv.shutdown()
    srv.server_close()


def get(url: str) -> tuple[int, bytes, Any]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:  # noqa: S310 - loopback test server
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def post(url: str, body: Any) -> int:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"}, method="POST")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def test_it_never_shows_the_machine_text_and_serves_only_what_it_should(server: tuple[str, Path]) -> None:
    base, folder = server
    status, body, headers = get(base + "/")
    assert status == 200 and MACHINE_TEXT.encode() not in body and "default-src 'none'" in headers["content-security-policy"]
    status, body, _ = get(base + "/api/lines")
    assert (
        status == 200 and MACHINE_TEXT.encode() not in body and [x["id"] for x in json.loads(body)["lines"]] == ["s001", "s002"]
    )
    assert get(base + "/crop/s001.jpg")[:2] == (200, b"\xff\xd8\xff-s001")
    for bad in (
        "/crop/s999.jpg",
        "/crop/../machine.json",
        "/crop/s001.jpg/../../machine.json",
        "/machine.json",
        "/transcriptions.json",
        "/etc/passwd",
    ):
        assert get(base + bad)[0] == 404, bad
    assert get(base + "/api/save")[0] == 404  # GET is not a save


def test_a_transcription_is_saved_as_typed_one_line_and_an_unreadable_line_keeps_no_text(server: tuple[str, Path]) -> None:
    base, folder = server
    assert post(base + "/api/save", {"id": "s001", "text": "  Teh  cat\n sat ", "unreadable": False}) == 200
    state = json.loads((folder / "transcriptions.json").read_text())["lines"]
    assert state["s001"] == {"text": "Teh cat sat", "unreadable": False}  # spelling kept as typed; only spacing tidied
    assert post(base + "/api/save", {"id": "s002", "text": "should be dropped", "unreadable": True}) == 200
    assert json.loads((folder / "transcriptions.json").read_text())["lines"]["s002"] == {"text": None, "unreadable": True}
    assert not list(folder.glob("*.tmp"))  # the atomic write left nothing behind
    status, body, _ = get(base + "/api/lines")
    assert [(x["id"], x["text"], x["unreadable"]) for x in json.loads(body)["lines"]] == [
        ("s001", "Teh cat sat", False),
        ("s002", None, True),
    ]


@pytest.mark.parametrize(
    "body",
    [
        {"id": "s003", "text": "x", "unreadable": False},
        {"id": "../x", "text": "x", "unreadable": False},
        {"id": "s001"},
        {"id": "s001", "text": 5, "unreadable": False},
        b"not json",
        b"",
    ],
)
def test_bad_saves_are_refused_and_change_nothing(server: tuple[str, Path], body: Any) -> None:
    base, folder = server
    before = (folder / "transcriptions.json").read_text()
    assert post(base + "/api/save", body) in (400, 404, 413)
    assert (folder / "transcriptions.json").read_text() == before
    assert post(base + "/api/elsewhere", {"id": "s001", "text": "x", "unreadable": False}) == 404
    assert post(base + "/api/save", b"x" * (mod.MAX_BODY + 1)) == 413


def test_it_listens_on_loopback_only_and_does_not_read_the_machine_file() -> None:
    src = (ROOT / "scripts" / "sample_transcriber.py").read_text()
    assert '("127.0.0.1", a.port)' in src and "0.0.0.0" not in src  # noqa: S104
    assert "machine.json" not in src.replace("`machine.json` is", "").split('"""', 2)[2]  # only the docstring mentions it
