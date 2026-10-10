"""No invisible or direction-changing characters in source files ("Trojan Source"). Such a character can make code or a test look
different from what it is. A visible escape sequence is fine; the character itself is not.

This file builds its character set from numeric code points, so it contains none of the characters it forbids.
Untracked-but-not-ignored files are scanned too: a guard that only reads `git ls-files` cannot see a new file (including itself)
until it is committed, which is exactly how this one first passed locally and then failed in CI.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_POINTS = [
    0x00AD,  # soft hyphen
    0x061C,  # arabic letter mark
    0x180E,  # mongolian vowel separator
    *range(0x200B, 0x2010),  # zero-width space/joiners, direction marks
    *range(0x202A, 0x202F),  # bidi embeddings and overrides
    *range(0x2060, 0x2070),  # word joiner, invisible operators, bidi isolates
    0xFEFF,  # byte order mark / zero-width no-break space
    *range(0xFFF9, 0xFFFC),  # interlinear annotation
    *range(0x00, 0x09),
    0x0B,
    0x0C,
    *range(0x0E, 0x20),
    *range(0x7F, 0xA0),  # C0/C1 control characters (tab, line feed, carriage return are allowed)
]
HIDDEN = re.compile("[" + "".join(re.escape(chr(c)) for c in CODE_POINTS) + "]")
SOURCE = {".py", ".ts", ".tsx", ".mjs", ".js", ".md", ".yml", ".yaml", ".toml", ".sh", ".json", ".css", ".txt", ".cfg", ".ini"}
SKIP_PREFIXES = ("data/", "spike/", "apps/web/pnpm-lock.yaml", "uv.lock", "services/ocr/requirements.lock.txt")


def sources() -> list[str]:
    cmd = ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    out = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True).stdout.decode()  # noqa: S603
    return sorted(
        {
            f
            for f in out.split("\0")
            if f and Path(f).suffix in SOURCE and not f.startswith(SKIP_PREFIXES) and (ROOT / f).is_file()
        }
    )


def test_no_source_file_contains_invisible_or_direction_changing_characters() -> None:
    files = sources()
    assert len(files) > 100
    assert "tests/test_no_hidden_unicode.py" in files  # the guard checks itself
    found = []
    for f in files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        for m in HIDDEN.finditer(text):
            found.append(f"{f}:{text.count(chr(10), 0, m.start()) + 1}: U+{ord(m.group()):04X}")
    assert found == []


def test_the_pattern_catches_what_it_should() -> None:
    for cp in (0x202E, 0x2066, 0x200B, 0xFEFF, 0x00AD, 0x0000, 0x2060, 0x061C):
        assert HIDDEN.search("a" + chr(cp) + "b"), hex(cp)
    for ok in [
        "plain",
        "caf" + chr(0xE9),
        chr(0x092A) + chr(0x094D),
        "tab\there",
        "x" + chr(0xB2) + " " + chr(0x2192) + " y",
        "line\nbreak",
    ]:
        assert not HIDDEN.search(ok), ok
