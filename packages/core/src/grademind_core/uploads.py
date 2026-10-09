"""Upload validation (spec §16): content sniffing (never the extension alone), a hard size limit enforced while reading,
filename sanitisation, and a content hash. Pure functions with no I/O beyond the stream being validated.

The sanitised filename is display metadata only. It never becomes part of a storage key, a path, a log line or an audit
entry: filenames of answer scripts often contain a student's name.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO, cast

CHUNK = 1024 * 1024
SPOOL_MAX_IN_MEMORY = 8 * 1024 * 1024


class UploadMime(StrEnum):
    PDF = "application/pdf"
    PNG = "image/png"
    JPEG = "image/jpeg"


# Answer booklets, question papers and marking schemes are PDFs or page images (spec §2).
DOCUMENT_TYPES = frozenset(UploadMime)

_EXTENSIONS: dict[UploadMime, frozenset[str]] = {
    UploadMime.PDF: frozenset({".pdf"}),
    UploadMime.PNG: frozenset({".png"}),
    UploadMime.JPEG: frozenset({".jpg", ".jpeg"}),
}


class UploadRejectedError(ValueError):
    """`code` is a stable machine-readable reason; `message` is safe to show to the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def sniff_mime(head: bytes) -> UploadMime | None:
    """Identify the type from the leading bytes only. Returns None for anything not on the allow-list."""
    if head.startswith(b"%PDF-"):
        return UploadMime.PDF
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return UploadMime.PNG
    if head.startswith(b"\xff\xd8\xff"):
        return UploadMime.JPEG
    return None


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_filename(name: str | None, max_len: int = 120) -> str:
    """Basename only (both separator styles), NFKC-normalised, restricted alphabet, no leading dots, bounded length."""
    base = re.split(r"[\\/]", unicodedata.normalize("NFKC", name or ""))[-1]
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")  # control and format characters
    base = _UNSAFE.sub("_", base).strip(" ._")
    base = re.sub(r"_{2,}", "_", base)
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: max_len - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else base[:max_len]
    return base or "upload"


def _extension(name: str) -> str:
    _, dot, ext = name.rpartition(".")
    return f".{ext.lower()}" if dot else ""


@dataclass
class ValidatedUpload:
    file: BinaryIO  # positioned at 0; the caller closes it
    size: int
    sha256: str
    mime: UploadMime
    filename: str  # sanitised display name


def validate_upload(
    stream: BinaryIO,
    filename: str | None,
    *,
    max_bytes: int,
    allowed: frozenset[UploadMime] = DOCUMENT_TYPES,
) -> ValidatedUpload:
    """Copy `stream` into a private spooled temp file, enforcing `max_bytes` as bytes arrive (a lying Content-Length or
    an endless body cannot exhaust memory or disk), then check the sniffed type against the allow-list and the extension."""
    out = cast(BinaryIO, tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_IN_MEMORY))  # noqa: SIM115 - returned to the caller
    try:
        h = hashlib.sha256()
        size = 0
        head = b""
        while chunk := stream.read(CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise UploadRejectedError("too_large", f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
            if len(head) < 16:
                head = (head + chunk)[:16]
            h.update(chunk)
            out.write(chunk)
        if size == 0:
            raise UploadRejectedError("empty", "The file is empty.")
        mime = sniff_mime(head)
        if mime is None or mime not in allowed:
            raise UploadRejectedError("unsupported_type", "Only PDF, PNG and JPEG files are accepted.")
        safe = sanitize_filename(filename)
        ext = _extension(safe)
        if ext and ext not in _EXTENSIONS[mime]:
            raise UploadRejectedError(
                "type_mismatch", f"The file name ends in {ext}, but the content is {mime.name}. Check you chose the right file."
            )
        out.seek(0)
        return ValidatedUpload(file=out, size=size, sha256=h.hexdigest(), mime=mime, filename=safe)
    except BaseException:
        out.close()
        raise
