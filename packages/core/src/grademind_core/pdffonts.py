"""Tamil and Devanagari in the PDF reports (D30.8): which text needs a font of its own, how it is shaped, and how that font is
embedded.

Why this exists: the report writer (`pdfwrite.py`) only knows the standard Latin fonts. These two scripts cannot be printed
by writing one glyph per character: letters change shape and order around vowel signs and conjuncts (a Tamil "கொ" is drawn as
three pieces, one of them before the consonant), so the text has to be *shaped* by a shaping engine (HarfBuzz, through
`uharfbuzz`) with a font that has the rules for the script. Fonts: Noto Sans Tamil and Noto Sans Devanagari (SIL OFL 1.1),
vendored in `fonts/`, checksummed (`SHA256SUMS`), and embedded whole in the PDF.

This module is the only one that imports the shaping library. It does not know about pages: `pdfwrite.py` asks it for runs,
widths and glyphs.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import uharfbuzz as hb

FONT_DIR = Path(__file__).parent / "fonts"

# script key -> (font file, code point ranges that belong to the script, HarfBuzz script tag, language)
SCRIPTS: dict[str, tuple[str, tuple[tuple[int, int], ...], str, str]] = {
    "tamil": ("NotoSansTamil-Regular.ttf", ((0x0B80, 0x0BFF),), "Taml", "ta"),
    "devanagari": ("NotoSansDevanagari-Regular.ttf", ((0x0900, 0x097F), (0xA8E0, 0xA8FF), (0x1CD0, 0x1CFF)), "Deva", "hi"),
}
JOINERS = (0x200C, 0x200D)  # zero-width non-joiner / joiner: part of the word they sit in, never a run of their own


class FontIntegrityError(RuntimeError):
    """A vendored font does not match `fonts/SHA256SUMS`: it is refused, never used."""


def script_of(ch: str) -> str | None:
    cp = ord(ch)
    for key, (_, ranges, _, _) in SCRIPTS.items():
        if any(lo <= cp <= hi for lo, hi in ranges):
            return key
    return None


def split_runs(text: str) -> list[tuple[str | None, str]]:
    """Cut `text` into runs: (script key, text) for Tamil / Devanagari, (None, text) for everything else.
    A joiner stays with the script run it follows, so a word is shaped as one piece."""
    runs: list[tuple[str | None, str]] = []
    for ch in text:
        key = script_of(ch)
        if key is None and ord(ch) in JOINERS and runs and runs[-1][0] is not None:
            key = runs[-1][0]
        if runs and runs[-1][0] == key:
            runs[-1] = (key, runs[-1][1] + ch)
        else:
            runs.append((key, ch))
    return runs


def has_indic(text: str) -> bool:
    return any(script_of(ch) is not None for ch in text)


@dataclass(frozen=True)
class Glyph:
    gid: int
    advance: float  # 1/1000 em, where the pen moves after this glyph
    dx: float  # 1/1000 em, offset of the drawing position from the pen (mark placement)
    dy: float
    cluster: int  # index (in code points) of the first character this glyph stands for


@dataclass(frozen=True)
class Shaped:
    glyphs: tuple[Glyph, ...]
    text: str

    @property
    def advance(self) -> float:
        return sum(g.advance for g in self.glyphs)

    @property
    def missing(self) -> int:
        """Glyphs that are the font's "no glyph" box (gid 0): a character the font cannot draw."""
        return sum(1 for g in self.glyphs if g.gid == 0)


def _checked(name: str) -> bytes:
    sums = {}
    for line in (FONT_DIR / "SHA256SUMS").read_text().splitlines():
        digest, _, fname = line.partition("  ")
        sums[fname.strip()] = digest.strip()
    data = (FONT_DIR / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != sums.get(name):
        raise FontIntegrityError(f"{name} does not match fonts/SHA256SUMS")
    return data


class IndicFont:
    """One embedded font: shaping, widths, and the objects that go into the PDF."""

    def __init__(self, key: str) -> None:
        fname, _, tag, lang = SCRIPTS[key]
        self.key, self.file, self.tag, self.lang = key, fname, tag, lang
        self.data = _checked(fname)
        self.face = hb.Face(self.data)
        self.font = hb.Font(self.face)
        self.upem: int = self.face.upem
        self.k: float = 1000.0 / self.upem

    def shape(self, text: str) -> Shaped:
        buf = hb.Buffer()
        buf.add_str(text)
        buf.script, buf.language, buf.direction = self.tag, self.lang, "ltr"
        hb.shape(self.font, buf)
        glyphs = tuple(
            Glyph(i.codepoint, p.x_advance * self.k, p.x_offset * self.k, p.y_offset * self.k, i.cluster)
            for i, p in zip(buf.glyph_infos, buf.glyph_positions, strict=True)
        )
        return Shaped(glyphs, text)

    def glyph_width(self, gid: int) -> float:
        """The advance the PDF viewer will use for this glyph (1/1000 em): the font's own, from `hmtx`."""
        advance: int = self.font.get_glyph_h_advance(gid)
        return float(advance * self.k)

    def compressed(self) -> bytes:
        return _deflate(self.data)

    def metrics(self) -> dict[str, int]:
        head, hhea = bytes(self.face.reference_table("head").data), bytes(self.face.reference_table("hhea").data)
        x_min, y_min, x_max, y_max = struct.unpack(">hhhh", head[36:44])
        ascent, descent = struct.unpack(">hh", hhea[4:8])
        k = self.k
        return {
            "x_min": round(x_min * k),
            "y_min": round(y_min * k),
            "x_max": round(x_max * k),
            "y_max": round(y_max * k),
            "ascent": round(ascent * k),
            "descent": round(descent * k),
        }


@cache
def _deflate(data: bytes) -> bytes:
    return zlib.compress(data, 9)


@cache
def font_for(key: str) -> IndicFont:
    return IndicFont(key)


def width(text: str, size: float, latin_em: float) -> float:
    """Width in points of `text` at `size`; every character outside Tamil/Devanagari advances `latin_em` em (0.6 for Courier)."""
    total = 0.0
    for key, part in split_runs(text):
        if key is None:
            total += len(part) * latin_em * size
        else:
            total += font_for(key).shape(part).advance * size / 1000.0
    return total
