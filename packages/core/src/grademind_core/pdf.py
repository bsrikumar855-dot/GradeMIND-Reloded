"""PDF and image helpers (pypdfium2 + Pillow). Used for the question-paper text layer (parser input) and for turning a
booklet into page images (Phase 2 step 2.4). No OCR: a scanned PDF simply has no text layer."""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass

import pypdfium2 as pdfium
from PIL import Image

MAX_PAGES = 200
RENDER_DPI = 150  # ~1240 x 1754 px for A4: enough to read handwriting at fit-width, small enough to stream
THUMB_WIDTH = 180
Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs (a 300-dpi A3 scan is ~17 MP)


class PdfError(ValueError):
    pass


def text_layer(data: bytes, max_pages: int = 50) -> str:
    """The embedded text of a PDF ('' for scans). Pages are separated by blank lines."""
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise PdfError("The PDF could not be opened.") from e
    try:
        parts = []
        for i in range(min(len(pdf), max_pages)):
            tp = pdf[i].get_textpage()
            parts.append(tp.get_text_range())
        return "\n\n".join(p.replace("\r\n", "\n").replace("\r", "\n") for p in parts).strip()
    finally:
        pdf.close()


@dataclass(frozen=True)
class PageImage:
    page_no: int
    jpeg: bytes
    width: int
    height: int
    thumb_jpeg: bytes


def _encode(img: Image.Image) -> tuple[bytes, bytes]:
    img = img.convert("RGB")
    full = io.BytesIO()
    img.save(full, format="JPEG", quality=88, optimize=True)
    t = img.copy()
    t.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2))
    thumb = io.BytesIO()
    t.save(thumb, format="JPEG", quality=80)
    return full.getvalue(), thumb.getvalue()


def page_images(data: bytes, mime: str) -> Iterator[PageImage]:
    """Booklet -> page images. PDF: every page rendered at RENDER_DPI. PNG/JPEG: one page."""
    if mime == "application/pdf":
        try:
            pdf = pdfium.PdfDocument(data)
        except pdfium.PdfiumError as e:
            raise PdfError("The PDF could not be opened.") from e
        try:
            if len(pdf) == 0:
                raise PdfError("The PDF has no pages.")
            if len(pdf) > MAX_PAGES:
                raise PdfError(f"The PDF has more than {MAX_PAGES} pages.")
            for i in range(len(pdf)):
                img = pdf[i].render(scale=RENDER_DPI / 72).to_pil()
                full, thumb = _encode(img)
                yield PageImage(i + 1, full, img.width, img.height, thumb)
        finally:
            pdf.close()
        return
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise PdfError("The image could not be read.") from e
    full, thumb = _encode(img)
    yield PageImage(1, full, img.width, img.height, thumb)


def crop_line(image_jpeg: bytes, box: tuple[int, int, int, int], pad: int = 4) -> tuple[bytes, list[int]]:
    """A COPY of the part of a page image around one machine-read line (JPEG), and the crop box actually used (padded, clamped to
    the page). The page image itself is only read, never changed (D19). Skewed lines are not straightened: the line's polygon
    and the page image hash are stored with every correction, so a dataset builder can re-crop with any method later."""
    try:
        img = Image.open(io.BytesIO(image_jpeg))
        img.load()
    except (OSError, Image.DecompressionBombError) as e:
        raise PdfError("The page image could not be read.") from e
    x0, y0, x1, y1 = box
    c = [max(0, x0 - pad), max(0, y0 - pad), min(img.width, x1 + pad), min(img.height, y1 + pad)]
    if c[2] <= c[0] or c[3] <= c[1]:
        raise PdfError("The line lies outside the page image.")
    out = io.BytesIO()
    img.convert("RGB").crop((c[0], c[1], c[2], c[3])).save(out, format="JPEG", quality=92)
    return out.getvalue(), c
