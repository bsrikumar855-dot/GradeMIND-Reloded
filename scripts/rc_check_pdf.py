"""Release-candidate check (5.7): read a generated PDF with a real PDF reader (pdfium, through grademind_core.pdf) and check it.

usage: uv run python scripts/rc_check_pdf.py FILE --state state.json [--expect TEXT ...]
Checks: the exam name (Tamil) and subject (Devanagari) from the state file are in the text exactly, and so are any
extra --expect texts; there is no "?" anywhere; the file is a PDF. Prints what was found; exits 1 on the first failure.
A report holds no student answer text, so none is involved here.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from grademind_core.pdf import text_layer


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--state", required=True)
    ap.add_argument("--expect", action="append", default=[])
    a = ap.parse_args(argv)
    data = Path(a.file).read_bytes()
    state = json.loads(Path(a.state).read_text(encoding="utf-8"))
    if not data.startswith(b"%PDF"):
        print(f"FAIL {a.file}: not a PDF")
        return 1
    text = " ".join(text_layer(data).split())
    wanted = [state["examName"], state["subject"], *a.expect]
    for w in wanted:
        if w not in text:
            print(f"FAIL {a.file}: the text does not contain {w!r}")
            return 1
        print(f"  found: {w}")
    if "?" in text:
        print(f"FAIL {a.file}: a question mark is in the text (a letter that could not be printed?)")
        return 1
    print(f"OK {Path(a.file).name}: {len(data)} bytes, {len(text)} characters read back, no '?'")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
