"""Local transcription tool for the pilot accuracy sample (D31.2). Standard library only; listens on 127.0.0.1 only.

usage: python3 scripts/sample_transcriber.py SAMPLE_FOLDER [--port 8766]      (the folder made by `cli pilot-sample`)

Shows one cropped line of handwriting at a time and a box to type what it says. It NEVER shows what the machine read:
`machine.json` is not opened by this program, so the transcription is blind. Type exactly what is written, including
spelling mistakes; do not correct and do not guess: if you cannot read it, tick "I cannot read this line". Saves after
every line into SAMPLE_FOLDER/transcriptions.json. Routes (a whitelist; no directory listing, no path outside the folder):
GET /, GET /api/lines, GET /crop/sNNN.jpg, POST /api/save.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SID = re.compile(r"^s\d{3,5}$")
MAX_BODY = 20_000
CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"
PAGE = (Path(__file__).resolve().parent / "sample_transcriber.html").read_bytes()


def make_handler(folder: Path) -> type[BaseHTTPRequestHandler]:
    tfile = folder / "transcriptions.json"

    def read_state() -> dict:
        return json.loads(tfile.read_text(encoding="utf-8"))

    def write_state(state: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, tfile)  # atomic: a crash never leaves half a file

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # no request log: there is nothing in it worth keeping
            pass

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(body)))
            self.send_header("cache-control", "no-store")
            self.send_header("x-content-type-options", "nosniff")
            self.send_header("content-security-policy", CSP)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/lines":
                st = read_state()["lines"]
                rows = [{"id": k, "text": v.get("text"), "unreadable": bool(v.get("unreadable"))} for k, v in sorted(st.items())]
                return self._send(200, json.dumps({"lines": rows}).encode(), "application/json")
            m = re.fullmatch(r"/crop/(s\d{3,5})\.jpg", self.path)
            if m and SID.match(m.group(1)):
                f = folder / "crops" / f"{m.group(1)}.jpg"
                if f.is_file():
                    return self._send(200, f.read_bytes(), "image/jpeg")
            self._send(404, b"not found")

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/save":
                return self._send(404, b"not found")
            n = int(self.headers.get("content-length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, b"too large")
            try:
                body = json.loads(self.rfile.read(n))
                sid, text, unreadable = body["id"], body["text"], bool(body["unreadable"])
                if not (isinstance(sid, str) and SID.match(sid) and isinstance(text, str)):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._send(400, b"bad request")
            state = read_state()
            if sid not in state["lines"]:
                return self._send(404, b"unknown line")
            text = " ".join(text.split())  # one line: no line breaks, no runs of spaces
            state["lines"][sid] = {"text": None if unreadable or not text else text, "unreadable": unreadable}
            write_state(state)
            self._send(200, b"ok")

    return Handler


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args(argv)
    folder = Path(a.folder).resolve()
    if not (folder / "transcriptions.json").is_file() or not (folder / "crops").is_dir():
        print("not a sample folder (made by `grademind_core.cli pilot-sample`)", file=sys.stderr)
        return 2
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(folder))
    print(f"Open http://127.0.0.1:{a.port}/ in a browser on this machine. Stop with Ctrl+C.")
    with contextlib.suppress(KeyboardInterrupt):
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
