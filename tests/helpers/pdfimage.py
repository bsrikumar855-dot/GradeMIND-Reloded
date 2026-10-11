"""Look at a generated PDF as a person would: render a page, measure the ink (pdfium + Pillow). Test helper only."""

from __future__ import annotations

import pypdfium2 as pdfium
from PIL import Image

SCALE = 3.0  # pixels per point when a page is rendered
PAGE_W = 595.28


def render_page(pdf: bytes, page: int = 0) -> Image.Image:
    doc = pdfium.PdfDocument(pdf)
    try:
        return doc[page].render(scale=SCALE).to_pil().convert("L")
    finally:
        doc.close()


def ink(img: Image.Image, top_pt: float, bottom_pt: float, left_pt: float = 0, right_pt: float = PAGE_W) -> Image.Image:
    """The part of the page between these distances (points) from the top-left corner, with ink as 255 and paper as 0."""
    box = (int(left_pt * SCALE), int(top_pt * SCALE), int(right_pt * SCALE), int(bottom_pt * SCALE))
    return img.crop(box).point(lambda v: 255 if v < 128 else 0)


def ink_box(mask: Image.Image) -> tuple[int, int, int, int] | None:
    return mask.getbbox()


def ink_pixels(mask: Image.Image) -> int:
    return int(mask.histogram()[255])
