"""A small, dependency-free PDF writer for reports (4.4): A4, the standard fonts (Helvetica, Helvetica-Bold, Courier), text and
rules, automatic page breaks, a footer with "Page i of n" on every page.

Why not a PDF library: the report needs are tiny and fixed, and a new third-party dependency in a deployment that pins and audits
every dependency (D26) costs more than ~150 lines of code that a test can read back with a real PDF reader.

Limits, stated: text is encoded as Windows-1252, so characters outside it (for example Devanagari or Tamil) are printed as "?" and
`Document.replaced` says so after rendering. Tables are laid out in the monospaced Courier font, so columns line up without font
metrics. Output is deterministic for the same content and the same `created` time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 48.0
FOOTER_Y = 28.0
COURIER_W = 0.6  # Courier advances 600/1000 of the font size per character
FONTS = {"Helvetica": "F1", "Helvetica-Bold": "F2", "Courier": "F3"}


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


@dataclass
class _Item:
    kind: str  # "text" | "rule"
    text: str = ""
    font: str = "Courier"
    size: float = 9.0
    indent: float = 0.0
    leading: float = 0.0


@dataclass
class Document:
    title: str
    footer: str  # shown on every page, left of "Page i of n"
    items: list[_Item] = field(default_factory=list)
    replaced: bool = False  # some character could not be printed and became '?'

    # -------------------------------------------------------------------------------- content

    def heading(self, text: str, size: float = 16.0) -> None:
        self.items.append(_Item("text", text, "Helvetica-Bold", size, 0.0, size * 1.5))

    def line(self, text: str, font: str = "Courier", size: float = 9.0, indent: float = 0.0, wrap: bool = True) -> None:
        """One paragraph. Monospaced text is wrapped to the page width; proportional text is not (keep it short)."""
        if font == "Courier" and wrap:
            width = max(10, int((PAGE_W - 2 * MARGIN - indent) / (COURIER_W * size)))
            for part in _wrap(text, width):
                self.items.append(_Item("text", part, font, size, indent, size * 1.35))
        else:
            self.items.append(_Item("text", text, font, size, indent, size * 1.35))

    def blank(self, n: float = 1.0) -> None:
        self.items.append(_Item("text", "", "Courier", 9.0, 0.0, 9.0 * 1.35 * n))

    def rule(self) -> None:
        self.items.append(_Item("rule", leading=8.0))

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
        objs: list[bytes] = [b""] * (3 + n * 2 + 3)  # filled below; 1 catalog, 2 pages, 3..5 fonts, then page/content pairs, info
        font_objs = {name: 3 + i for i, name in enumerate(FONTS)}
        first_page = 6
        info_no = first_page + 2 * n
        kids = " ".join(f"{first_page + 2 * i} 0 R" for i in range(n))
        objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[1] = f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode()
        for name, no in font_objs.items():
            objs[no - 1] = f"<< /Type /Font /Subtype /Type1 /BaseFont /{name} /Encoding /WinAnsiEncoding >>".encode()
        res = " ".join(f"/{key} {font_objs[name]} 0 R" for name, key in FONTS.items())
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
                if it.text:
                    raw, lost = _encode(it.text)
                    self.replaced = self.replaced or lost
                    ops.append(
                        f"BT /{FONTS[it.font]} {it.size:.1f} Tf {MARGIN + it.indent:.2f} {y + it.leading * 0.25:.2f} Td ".encode()
                        + _literal(raw)
                        + b" Tj ET"
                    )
            raw, lost = _encode(f"{self.footer}   Page {i + 1} of {n}")
            self.replaced = self.replaced or lost
            ops.append(f"BT /F1 7.5 Tf {MARGIN:.2f} {FOOTER_Y:.2f} Td ".encode() + _literal(raw) + b" Tj ET")
            stream = b"\n".join(ops)
            page_no = first_page + 2 * i
            objs[page_no - 1] = (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] /Resources << /Font << {res} >> >> "
                f"/Contents {page_no + 1} 0 R >>"
            ).encode()
            objs[page_no] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        title_raw, _ = _encode(self.title)
        stamp = created.astimezone(UTC).strftime("D:%Y%m%d%H%M%SZ")
        objs[info_no - 1] = (
            b"<< /Title " + _literal(title_raw) + b" /Producer (GradeMIND) /CreationDate (" + stamp.encode() + b") >>"
        )
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
