"""The report PDF writer, checked by reading its output back with a real PDF reader (pdfium)."""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given, settings
from hypothesis import strategies as st
from pdftext import pdf_pages, pdf_text, pdf_title

from grademind_core.pdfwrite import Document, _wrap

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def doc(footer: str = "Footer text") -> Document:
    return Document(title="A title", footer=footer)


def test_a_document_reads_back_as_written() -> None:
    d = doc("Snapshot 1234")
    d.heading("Result sheet")
    d.line("Student reference: S-001")
    d.line("Marks   2 / 5", "Courier", 12, wrap=False)
    pdf = d.render(T0)
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF")
    text = pdf_text(pdf)
    assert "Result sheet" in text and "Student reference: S-001" in text and "Marks 2 / 5" in text
    assert "Snapshot 1234" in text and "Page 1 of 1" in text
    assert pdf_title(pdf) == "A title"


def test_parentheses_backslashes_and_non_ascii_survive_or_are_marked() -> None:
    d = doc()
    d.line("f(x) = (a\\b) and caf" + chr(0xE9) + " and 5 % off")
    pdf = d.render(T0)
    text = pdf_text(pdf)
    assert "f(x) = (a\\b)" in text and ("caf" + chr(0xE9)) in text and "5 % off" in text
    assert d.replaced is False
    e = doc()
    e.line("Tamil: அஆ")  # outside Windows-1252
    e.render(T0)
    assert e.replaced is True and "?" in pdf_text(e.render(T0))


def test_long_documents_break_into_pages_and_every_page_is_numbered() -> None:
    d = doc("Footer")
    for i in range(160):
        d.line(f"line {i:03d}")
    pdf = d.render(T0)
    pages = pdf_pages(pdf)
    assert len(pages) >= 3
    for i, page in enumerate(pages, start=1):
        assert f"Page {i} of {len(pages)}" in page and "Footer" in page
    assert "line 000" in pages[0] and "line 159" in pages[-1]
    joined = pdf_text(pdf)
    assert all(f"line {i:03d}" in joined for i in range(160))  # nothing lost at a page break


def test_output_is_deterministic_for_the_same_content_and_time() -> None:
    def make() -> bytes:
        d = doc()
        d.heading("H")
        d.line("same")
        d.rule()
        return d.render(T0)

    assert make() == make()
    other = doc()
    other.heading("H")
    other.line("same")
    other.rule()
    assert other.render(datetime(2026, 10, 11, tzinfo=UTC)) != make()  # only the creation stamp differs


def test_monospaced_paragraphs_wrap_to_the_page_and_keep_their_indent() -> None:
    words = " ".join(["word"] * 80)
    d = doc()
    d.line("  " + words, "Courier", 9, indent=0)
    text = pdf_text(d.render(T0))
    lines = [ln for ln in text.splitlines() if "word" in ln]
    assert len(lines) >= 3 and all(len(ln) <= 95 for ln in lines)


def test_wrap_helper() -> None:
    assert _wrap("", 10) == [""]
    assert _wrap("aaa bbb ccc", 7) == ["aaa bbb", "ccc"]
    assert _wrap("  indented text", 10) == ["  indented", "  text"]
    assert _wrap("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    assert all(len(ln) <= 10 for ln in _wrap("a bb " * 30 + "z" * 40, 10))


@settings(max_examples=40, deadline=None)
@given(st.lists(st.text(max_size=120), max_size=30))
def test_any_text_gives_a_pdf_a_reader_accepts(lines: list[str]) -> None:
    d = doc()
    for ln in lines:
        d.line(ln)
        d.line(ln, "Helvetica", 10, wrap=False)
    pdf = d.render(T0)
    pages = pdf_pages(pdf)
    assert len(pages) >= 1 and f"Page {len(pages)} of {len(pages)}" in pages[-1]
