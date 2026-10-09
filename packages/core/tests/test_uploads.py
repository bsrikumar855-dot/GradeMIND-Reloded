"""Upload validation (spec §16): sniffing beats the extension, size enforced while reading, filenames sanitised."""

from __future__ import annotations

import hashlib
import io

import pytest

from grademind_core.uploads import UploadMime, UploadRejectedError, sanitize_filename, sniff_mime, validate_upload

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.mark.parametrize(("data", "mime"), [(PDF, UploadMime.PDF), (PNG, UploadMime.PNG), (JPEG, UploadMime.JPEG)])
def test_accepts_allowed_types_by_content(data: bytes, mime: UploadMime) -> None:
    v = validate_upload(io.BytesIO(data), "scan", max_bytes=10_000)
    assert v.mime == mime and v.size == len(data) and v.sha256 == hashlib.sha256(data).hexdigest()
    assert v.file.read() == data
    v.file.close()


@pytest.mark.parametrize(
    "data",
    [
        b"MZ\x90\x00 windows executable",
        b"<html><script>alert(1)</script></html>",
        b"PK\x03\x04 zip or docx",
        b"GIF89a....",
        b"  %PDF-1.7 leading whitespace is not a PDF header",
    ],
)
def test_rejects_disallowed_content_even_with_allowed_extension(data: bytes) -> None:
    with pytest.raises(UploadRejectedError) as e:
        validate_upload(io.BytesIO(data), "answers.pdf", max_bytes=10_000)
    assert e.value.code == "unsupported_type"


def test_rejects_extension_that_contradicts_content() -> None:
    with pytest.raises(UploadRejectedError) as e:
        validate_upload(io.BytesIO(PNG), "booklet.pdf", max_bytes=10_000)
    assert e.value.code == "type_mismatch"


def test_size_limit_is_enforced_while_reading() -> None:
    class Endless(io.RawIOBase):
        reads = 0

        def readable(self) -> bool:
            return True

        def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
            Endless.reads += 1
            b[: len(b)] = b"%" * len(b)
            return len(b)

    with pytest.raises(UploadRejectedError) as e:
        validate_upload(io.BufferedReader(Endless()), "x.pdf", max_bytes=3 * 1024 * 1024)
    assert e.value.code == "too_large"
    assert Endless.reads < 64  # stopped soon after the limit; did not consume the stream


def test_rejects_empty() -> None:
    with pytest.raises(UploadRejectedError) as e:
        validate_upload(io.BytesIO(b""), "x.pdf", max_bytes=10)
    assert e.value.code == "empty"


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("/abs/path/answer.pdf", "answer.pdf"),
        ("  .hidden.pdf", "hidden.pdf"),
        ("ans\x00wer‮.pdf", "answer.pdf"),
        ("a<b>c|d:e*f?.pdf", "a_b_c_d_e_f_.pdf"),
        ("", "upload"),
        (None, "upload"),
        ("..", "upload"),
        ("ｆｕｌｌｗｉｄｔｈ.pdf", "fullwidth.pdf"),
    ],
)
def test_sanitize_filename(raw: str | None, safe: str) -> None:
    assert sanitize_filename(raw) == safe


def test_sanitize_filename_bounds_length_and_keeps_extension() -> None:
    out = sanitize_filename("a" * 500 + ".pdf")
    assert len(out) == 120 and out.endswith(".pdf")


def test_sniff_mime_unknown() -> None:
    assert sniff_mime(b"") is None and sniff_mime(b"%PD") is None
