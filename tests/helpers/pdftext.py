"""Read a generated PDF back with a real PDF reader (pdfium): tests check what a person would see, not our own writer."""

from __future__ import annotations

import pypdfium2 as pdfium


def _norm(text: str) -> str:
    """pdfium returns CRLF line ends and collapses runs of spaces; compare on single spaces and plain newlines."""
    return "\n".join(" ".join(line.split()) for line in text.replace("\r\n", "\n").split("\n"))


def pdf_pages(data: bytes) -> list[str]:
    doc = pdfium.PdfDocument(data)
    try:
        return [_norm(doc[i].get_textpage().get_text_range()) for i in range(len(doc))]
    finally:
        doc.close()


def pdf_text(data: bytes) -> str:
    return "\n".join(pdf_pages(data))


def pdf_title(data: bytes) -> str:
    doc = pdfium.PdfDocument(data)
    try:
        meta = doc.get_metadata_dict()
        return str(meta.get("Title", ""))
    finally:
        doc.close()
