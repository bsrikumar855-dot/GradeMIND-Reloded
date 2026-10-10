"""No invisible or direction-changing characters in source files ("Trojan Source"). Such a character can make code or a test look
different from what it is. Escapes (a visible backslash-u sequence) are fine; the characters themselves are not."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# bidi overrides/isolates, zero-width and format characters, soft hyphen, BOM, interlinear annotation, C0/C1 controls
HIDDEN = re.compile("[­؜᠎​-‏‪-‮⁠-⁯﻿￹-￻\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
SOURCE = {".py", ".ts", ".tsx", ".mjs", ".js", ".md", ".yml", ".yaml", ".toml", ".sh", ".json", ".css", ".txt", ".cfg", ".ini"}
SKIP_PREFIXES = ("data/", "spike/", "apps/web/pnpm-lock.yaml", "uv.lock", "services/ocr/requirements.lock.txt")


def tracked_sources() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode()  # noqa: S607
    return [f for f in out.split("\0") if f and Path(f).suffix in SOURCE and not f.startswith(SKIP_PREFIXES)]


def test_no_source_file_contains_invisible_or_direction_changing_characters() -> None:
    files = tracked_sources()
    assert len(files) > 100
    found = []
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        for m in HIDDEN.finditer(text):
            found.append(f"{f}:{text.count(chr(10), 0, m.start()) + 1}: U+{ord(m.group()):04X}")
    assert found == []


def test_the_pattern_catches_what_it_should() -> None:
    for ch in ["‮", "⁦", "​", "﻿", "­", "\x00", "⁠"]:
        assert HIDDEN.search(f"a{ch}b"), hex(ord(ch))
    for ok in ["plain", "café", "प्रकाश", "tab\there", "x² → y"]:
        assert not HIDDEN.search(ok), ok
