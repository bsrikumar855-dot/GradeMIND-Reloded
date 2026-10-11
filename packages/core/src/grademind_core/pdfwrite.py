"""A small PDF writer for reports (4.4, 5.5): A4, the standard fonts (Helvetica, Helvetica-Bold, Courier), text and rules,
automatic page breaks, a footer with "Page i of n" on every page, and Tamil and Devanagari (D30.8).

Why not a PDF library: the report needs are tiny and fixed, and a new third-party dependency in a deployment that pins and
audits every dependency (D26) costs more than a few hundred lines of code that a test can read back with a real PDF reader.
The one thing that cannot be done by hand is *shaping* Tamil and Devanagari, so that is the one library added (`uharfbuzz`,
in `pdffonts.py`).

How text is printed: Latin text uses the standard fonts (Windows-1252). A run of Tamil or Devanagari uses the embedded Noto
font for that script, shaped by HarfBuzz and written as glyphs, with the real characters stored alongside (ActualText and
ToUnicode) so the text can be searched, copied and read back. A character that no font here can print (another script) is
printed as "?" and `Document.replaced` says so after rendering; so does a character the Noto font has no glyph for. Tables
are laid out in the monospaced Courier font, so Latin columns line up without font metrics; a cell with Tamil or Devanagari
text is measured, and right-aligned cells are placed by measured width. Output is deterministic for the same content and the
same `created` time.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime

from grademind_core import pdffonts

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 48.0
FOOTER_Y = 28.0
COURIER_W = 0.6  # Courier advances 600/1000 of the font size per character
FONTS = {"Helvetica": "F1", "Helvetica-Bold": "F2", "Courier": "F3"}
INDIC_FIRST = 4  # the embedded fonts are /F4, /F5 ... in the order of pdffonts.SCRIPTS


def _encode(text: str) -> tuple[bytes, bool]:
    """Windows-1252 bytes of `text` with control characters turned into spaces. Second value: something was replaced by '?'."""
    clean = "".join(" " if (ord(c) < 32 or ord(c) == 127) else c for c in text)
    raw = clean.encode("cp1252", errors="replace")
    return raw, raw.count(b"?") > clean.count("?")


def _literal(raw: bytes) -> bytes:
    out = bytearray(b"(")
    for b in raw:
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        elif b < 32 or b > 126:
            out += b"\\%03o" % b
        else:
            out.append(b)
    out += b")"
    return bytes(out)


def _utf16_hex(text: str) -> str:
    """A PDF text string with any characters: UTF-16BE with a byte-order mark, as hex."""
    return "FEFF" + text.encode("utf-16-be").hex().upper()


def _num(x: float) -> str:
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass
class _Cell:
    text: str
    col: float = 0.0  # position in Courier columns from the left margin (+ the item's indent)
    align: str = "l"  # "l": the text starts at `col`; "r": the text ends at `col`


@dataclass
class _Item:
    kind: str  # "text" | "rule"
    text: str = ""
    font: str = "Courier"
    size: float = 9.0
    indent: float = 0.0
    leading: float = 0.0
    cells: tuple[_Cell, ...] = ()


@dataclass
class _Used:
    """What an embedded font was asked to draw: glyph widths and the characters they stand for, for /W and /ToUnicode."""

    widths: dict[int, float] = field(default_factory=dict)
    unicode: dict[int, str] = field(default_factory=dict)


@dataclass
class Document:
    title: str
    footer: str  # shown on every page, left of "Page i of n"
    items: list[_Item] = field(default_factory=list)
    replaced: bool = False  # some character could not be printed and became '?' (or a font had no glyph for it)

    # -------------------------------------------------------------------------------- content

    def heading(self, text: str, size: float = 16.0) -> None:
        self.items.append(_Item("text", text, "Helvetica-Bold", size, 0.0, size * 1.5))

    def line(self, text: str, font: str = "Courier", size: float = 9.0, indent: float = 0.0, wrap: bool = True) -> None:
        """One paragraph. Monospaced text is wrapped to the page width; proportional text is not (keep it short)."""
        if font == "Courier" and wrap:
            room = PAGE_W - 2 * MARGIN - indent
            if pdffonts.has_indic(text):
                parts = _wrap_measured(text, room, size)
            else:
                parts = _wrap(text, max(10, int(room / (COURIER_W * size))))
            for part in parts:
                self.items.append(_Item("text", part, font, size, indent, size * 1.35))
        else:
            self.items.append(_Item("text", text, font, size, indent, size * 1.35))

    def row(self, cells: list[tuple[str, float, str]], size: float = 9.0, wrap_last: bool = False) -> None:
        """A table row in Courier: each cell is (text, column, "l" | "r"); columns count Courier characters from the left margin.
        A right-aligned cell ends at its column whatever script its text is in. With `wrap_last`, the last (left-aligned) cell
        continues on the lines below, at its own column, when it is longer than the page."""
        built = [_Cell(*c) for c in cells]
        extra: list[str] = []
        if wrap_last and built and built[-1].align == "l":
            last = built[-1]
            room = PAGE_W - 2 * MARGIN - last.col * COURIER_W * size
            parts = (
                _wrap_measured(last.text, room, size)
                if pdffonts.has_indic(last.text)
                else _wrap(last.text, max(10, int(room / (COURIER_W * size))))
            )
            last.text, extra = parts[0], parts[1:]
        leading = size * 1.35
        self.items.append(_Item("text", "", "Courier", size, 0.0, leading, tuple(built)))
        for more in extra:
            self.items.append(_Item("text", "", "Courier", size, 0.0, leading, (_Cell(more, built[-1].col, "l"),)))

    def blank(self, n: float = 1.0) -> None:
        self.items.append(_Item("text", "", "Courier", 9.0, 0.0, 9.0 * 1.35 * n))

    def rule(self) -> None:
        self.items.append(_Item("rule", leading=8.0))

    # -------------------------------------------------------------------------------- text on a page

    def _text_ops(
        self, text: str, font: str, size: float, x: float, y: float, used: dict[str, _Used], align: str = "l"
    ) -> bytes | None:
        """The content-stream operators that print `text` with its left edge (or right edge, align "r") at x."""
        if not text:
            return None
        if align == "r":
            x -= pdffonts.width(text, size, COURIER_W)
        ops: list[bytes] = [f"BT {_num(x)} {_num(y)} Td".encode()]
        for key, part in pdffonts.split_runs(text):
            if key is None:
                raw, lost = _encode(part)
                self.replaced = self.replaced or lost
                ops.append(f"/{FONTS[font]} {_num(size)} Tf ".encode() + _literal(raw) + b" Tj")
            else:
                ops.append(self._indic_ops(key, part, size, used.setdefault(key, _Used())))
        ops.append(b"ET")
        return b" ".join(ops)

    def _indic_ops(self, key: str, text: str, size: float, used: _Used) -> bytes:
        font = pdffonts.font_for(key)
        shaped = font.shape(text)
        if shaped.missing:
            self.replaced = True
        res = f"F{INDIC_FIRST + list(pdffonts.SCRIPTS).index(key)}"
        # which characters each glyph stands for (only when a cluster is exactly one glyph; ActualText covers the rest)
        starts = sorted({g.cluster for g in shaped.glyphs})
        span = {c: text[c : (starts[i + 1] if i + 1 < len(starts) else len(text))] for i, c in enumerate(starts)}
        per_cluster: dict[int, int] = {}
        for g in shaped.glyphs:
            per_cluster[g.cluster] = per_cluster.get(g.cluster, 0) + 1
        out: list[bytes] = [f"/Span << /ActualText <{_utf16_hex(text)}> >> BDC /{res} {_num(size)} Tf".encode()]
        items: list[str] = []  # the open TJ array
        pen = 0.0  # where the glyphs should be, in 1/1000 em from the start of the run
        at = 0.0  # where the viewer's own advance has left the text position

        def flush() -> None:
            nonlocal items
            if items:
                out.append(("[" + " ".join(items) + "] TJ").encode())
                items = []

        for g in shaped.glyphs:
            w = font.glyph_width(g.gid)
            used.widths[g.gid] = w
            if per_cluster[g.cluster] == 1:
                used.unicode.setdefault(g.gid, span[g.cluster])
            move = (pen + g.dx) - at
            if abs(move) > 1e-6:
                items.append(_num(-move))
            if g.dy:
                flush()
                out.append(f"{_num(g.dy * size / 1000.0)} Ts".encode())
                out.append(f"[<{g.gid:04X}>] TJ 0 Ts".encode())
            else:
                items.append(f"<{g.gid:04X}>")
            at = pen + g.dx + w
            pen += g.advance
        if abs(pen - at) > 1e-6:
            items.append(_num(-(pen - at)))
        flush()
        out.append(b"EMC")
        return b" ".join(out)

    # -------------------------------------------------------------------------------- output

    def render(self, created: datetime | None = None) -> bytes:
        created = created or datetime.now(UTC)
        pages: list[list[_Item]] = [[]]
        y = PAGE_H - MARGIN
        for it in self.items:
            need = it.leading or 8.0
            if y - need < MARGIN + 14:  # keep clear of the footer
                pages.append([])
                y = PAGE_H - MARGIN
            pages[-1].append(it)
            y -= need
        n = len(pages)
        used: dict[str, _Used] = {}
        streams: list[bytes] = []
        for i, items in enumerate(pages):
            ops: list[bytes] = []
            y = PAGE_H - MARGIN
            for it in items:
                if it.kind == "rule":
                    yy = y - 3
                    ops.append(f"0.6 w {MARGIN:.2f} {yy:.2f} m {PAGE_W - MARGIN:.2f} {yy:.2f} l S".encode())
                    y -= it.leading
                    continue
                y -= it.leading
                base = y + it.leading * 0.25
                cells = it.cells or (_Cell(it.text),)
                for c in cells:
                    x = MARGIN + it.indent + c.col * COURIER_W * it.size if it.cells else MARGIN + it.indent
                    op = self._text_ops(c.text, it.font, it.size, x, base, used, c.align)
                    if op:
                        ops.append(op)
            foot = self._text_ops(f"{self.footer}   Page {i + 1} of {n}", "Helvetica", 7.5, MARGIN, FOOTER_Y, used)
            if foot:
                ops.append(foot)
            streams.append(b"\n".join(ops))

        keys = [k for k in pdffonts.SCRIPTS if k in used]  # embedded fonts in a fixed order
        font_objs = {name: 3 + i for i, name in enumerate(FONTS)}
        first_page = 6
        info_no = first_page + 2 * n
        embed_first = info_no + 1
        type0 = {k: embed_first + 5 * j + 4 for j, k in enumerate(keys)}  # object number of each embedded font's top-level font
        objs: list[bytes] = [b""] * (info_no + 5 * len(keys))
        kids = " ".join(f"{first_page + 2 * i} 0 R" for i in range(n))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
        for name, no in font_objs.items():
            objs[no - 1] = f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode()
        res = " ".join(f"/{key} {font_objs[name]} 0 R" for name, key in FONTS.items())
        res += "".join(f" /F{INDIC_FIRST + list(pdffonts.SCRIPTS).index(k)} {no} 0 R" for k, no in type0.items())
        for i, stream in enumerate(streams):
            page_no = first_page + 2 * i
            objs[page_no - 1] = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources << /Font << {res} >> >> "
                f"/Contents {page_no + 1} 0 R >>"
            ).encode()
            objs[page_no] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        stamp = created.astimezone(UTC).strftime("D:%Y%m%d%H%M%SZ")
        objs[info_no - 1] = f"<< /Title <{_utf16_hex(self.title)}> /Producer (GradeMIND) /CreationDate ({stamp}) >>".encode()
        for j, k in enumerate(keys):
            first = embed_first + 5 * j
            objs[first - 1 : first + 4] = _embedded_font(pdffonts.font_for(k), used[k], first)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {info_no} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(out)


def columns(text: str, size: float = 9.0) -> float:
    """How many Courier columns `text` takes at `size`; Tamil and Devanagari are measured with their own font."""
    return pdffonts.width(text, size, COURIER_W) / (COURIER_W * size)


def _embedded_font(font: pdffonts.IndicFont, used: _Used, first: int) -> list[bytes]:
    """The five objects of an embedded font, numbered first .. first+4: file, descriptor, CID font, ToUnicode, top-level font."""
    name = font.file.removesuffix(".ttf").replace("-", "")
    m = font.metrics()
    data = font.compressed()
    file_obj = (
        f"<< /Length {len(data)} /Length1 {len(font.data)} /Filter /FlateDecode >>\nstream\n".encode() + data + b"\nendstream"
    )
    desc = (
        f"<< /Type /FontDescriptor /FontName /{name} /Flags 4 /FontBBox [{m['x_min']} {m['y_min']} {m['x_max']} {m['y_max']}] "
        f"/ItalicAngle 0 /Ascent {m['ascent']} /Descent {m['descent']} /CapHeight {m['ascent']} /StemV 80 "
        f"/FontFile2 {first} 0 R >>"
    ).encode()
    widths = " ".join(f"{gid} [{_num(w)}]" for gid, w in sorted(used.widths.items()))
    cid = (
        f"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /{name} "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        f"/FontDescriptor {first + 1} 0 R /CIDToGIDMap /Identity /DW 1000 /W [{widths}] >>"
    ).encode()
    chars = sorted(used.unicode.items())
    blocks = []
    for k in range(0, len(chars), 100):
        part = chars[k : k + 100]
        blocks.append(
            f"{len(part)} beginbfchar\n"
            + "\n".join(f"<{g:04X}> <{t.encode('utf-16-be').hex().upper()}>" for g, t in part)
            + "\nendbfchar"
        )
    cmap = (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        + "\n".join(blocks)
        + "\nendcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode()
    tounicode = b"<< /Length " + str(len(cmap)).encode() + b" >>\nstream\n" + cmap + b"\nendstream"
    top = (
        f"<< /Type /Font /Subtype /Type0 /BaseFont /{name} /Encoding /Identity-H /DescendantFonts [{first + 2} 0 R] "
        f"/ToUnicode {first + 3} 0 R >>"
    ).encode()
    return [file_obj, desc, cid, tounicode, top]


def _wrap(text: str, width: int) -> list[str]:
    """Wrap a paragraph at spaces to `width` characters. Leading spaces are kept as the indent of every line, runs of spaces
    inside the text collapse, and a word longer than a line is broken."""
    if not text.strip():
        return [""]
    lead = len(text) - len(text.lstrip(" "))
    prefix = " " * lead
    room = max(1, width - lead)
    lines: list[str] = []
    cur = ""
    for word in text.split():
        while len(word) > room:
            if cur:
                lines.append(prefix + cur)
                cur = ""
            lines.append(prefix + word[:room])
            word = word[room:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= room:
            cur += " " + word
        else:
            lines.append(prefix + cur)
            cur = word
    lines.append(prefix + cur)
    return lines


def _wrap_measured(text: str, room: float, size: float) -> list[str]:
    """Like `_wrap`, for text with Tamil or Devanagari in it: `room` is in points and every word is measured, because these
    letters are not as wide as a Courier character. A word longer than a line is broken between letters, never inside one."""
    if not text.strip():
        return [""]
    lead = len(text) - len(text.lstrip(" "))
    prefix = " " * lead
    room -= lead * COURIER_W * size
    room = max(room, 4 * COURIER_W * size)

    def w(s: str) -> float:
        return pdffonts.width(s, size, COURIER_W)

    lines: list[str] = []
    cur = ""
    for word in text.split():
        while w(word) > room:
            if cur:
                lines.append(prefix + cur)
                cur = ""
            cut = 1
            while cut < len(word) and w(word[: cut + 1]) <= room:
                cut += 1
            while cut < len(word) and (unicodedata.category(word[cut])[0] == "M" or ord(word[cut]) in pdffonts.JOINERS):
                cut += 1  # do not leave a vowel sign or a joiner at the start of the next line
            lines.append(prefix + word[:cut])
            word = word[cut:]
        if not cur:
            cur = word
        elif w(cur + " " + word) <= room:
            cur += " " + word
        else:
            lines.append(prefix + cur)
            cur = word
    lines.append(prefix + cur)
    return lines
