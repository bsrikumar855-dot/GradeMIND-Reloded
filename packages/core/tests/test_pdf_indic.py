"""5.5 (D30.8): Tamil and Devanagari in the PDF reports. Read back with a real PDF reader (pdfium), and *looked at*: the page is
rendered to an image and the ink is measured, so a "?" or a box where a letter should be cannot pass."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pdfimage import SCALE, ink, ink_box, ink_pixels, render_page
from pdftext import pdf_pages, pdf_text, pdf_title

from grademind_core import pdffonts
from grademind_core.pdfwrite import MARGIN, PAGE_H, PAGE_W, Document

T0 = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
TAMIL = "தமிழ் தேர்வு கொடு ஸ்ரீ"
DEVANAGARI = "हिन्दी परीक्षा क्षत्रिय किताब"


def one_line(text: str, font: str = "Helvetica", size: float = 14.0) -> bytes:
    d = Document("t", "f")
    d.line(text, font, size, wrap=False)
    return d.render(T0)


# ------------------------------------------------------------------------------------------------ the fonts themselves


def test_vendored_fonts_match_their_checksums_and_ship_their_licence() -> None:
    sums = dict(reversed(ln.split("  ")) for ln in (pdffonts.FONT_DIR / "SHA256SUMS").read_text().split("\n") if ln)
    assert set(sums) == {
        "NotoSansTamil-Regular.ttf",
        "NotoSansDevanagari-Regular.ttf",
        "OFL-NotoSansTamil.txt",
        "OFL-NotoSansDevanagari.txt",
    }
    for name in ("NotoSansTamil-Regular.ttf", "NotoSansDevanagari-Regular.ttf"):
        assert pdffonts._checked(name)  # raises on a mismatch
    for name in ("OFL-NotoSansTamil.txt", "OFL-NotoSansDevanagari.txt"):
        assert "SIL OPEN FONT LICENSE Version 1.1" in (pdffonts.FONT_DIR / name).read_text()


def test_a_font_that_does_not_match_its_checksum_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for f in pdffonts.FONT_DIR.iterdir():
        shutil.copy(f, tmp_path / f.name)
    monkeypatch.setattr(pdffonts, "FONT_DIR", tmp_path)
    assert pdffonts._checked("NotoSansTamil-Regular.ttf")
    with (tmp_path / "NotoSansTamil-Regular.ttf").open("r+b") as fh:
        fh.seek(1000)
        fh.write(b"\x00\x01\x02\x03")
    with pytest.raises(pdffonts.FontIntegrityError):
        pdffonts._checked("NotoSansTamil-Regular.ttf")
    shutil.copy(pdffonts.FONT_DIR / "NotoSansTamil-Regular.ttf", tmp_path / "Unlisted.ttf")
    with pytest.raises(pdffonts.FontIntegrityError):  # a file that is not in the list is refused too
        pdffonts._checked("Unlisted.ttf")


def test_runs_are_cut_by_script_and_joiners_stay_with_their_word() -> None:
    assert pdffonts.split_runs("abc") == [(None, "abc")]
    assert pdffonts.split_runs("a தமிழ் b") == [(None, "a "), ("tamil", "தமிழ்"), (None, " b")]
    zwj_word = (
        "\u0915\u094d\u200d\u0937"  # ka + virama + ZERO WIDTH JOINER + ssa (written as escapes: no hidden characters in source)
    )
    assert pdffonts.split_runs(zwj_word + " x") == [("devanagari", zwj_word), (None, " x")]  # the joiner did not start a run
    assert pdffonts.split_runs("अ தமிழ்") == [("devanagari", "अ"), (None, " "), ("tamil", "தமிழ்")]
    assert pdffonts.split_runs("") == []
    assert pdffonts.has_indic("x ஆ") and not pdffonts.has_indic("plain 123 café")


def test_letters_are_shaped_not_copied_one_by_one() -> None:
    ta, hi = pdffonts.font_for("tamil"), pdffonts.font_for("devanagari")
    assert len(ta.shape("க்ஷ").glyphs) == 1  # a conjunct: three characters, one glyph
    assert len(ta.shape("கொ").glyphs) == 3  # a vowel sign drawn on both sides of the consonant
    assert len(hi.shape("क्षत्रिय").glyphs) < len("क्षत्रिय")
    assert ta.shape(TAMIL).missing == 0 and hi.shape(DEVANAGARI).missing == 0
    assert ta.shape("஄").missing == 1  # an unassigned code point: the font's "no glyph" box is detected


# ------------------------------------------------------------------------------------------------ what comes out of a PDF


def test_tamil_and_devanagari_read_back_exactly_with_no_question_marks() -> None:
    d = Document("Result தமிழ்", f"Footer {TAMIL}")
    d.heading("Result sheet")
    d.line(f"{TAMIL}  -  {DEVANAGARI}", "Helvetica", 11, wrap=False)
    d.line(f"Student reference: {DEVANAGARI}")
    d.row([(f"Question {TAMIL}", 0, "l"), ("2 / 5", 72, "r")])
    pdf = d.render(T0)
    text = pdf_text(pdf)
    assert d.replaced is False
    assert f"{TAMIL} - {DEVANAGARI}" in text and f"Student reference: {DEVANAGARI}" in text
    assert f"Question {TAMIL} 2 / 5" in text and f"Footer {TAMIL} Page 1 of 1" in text
    assert "?" not in text
    assert pdf_title(pdf) == "Result தமிழ்"  # the document title keeps its characters too


def test_the_page_shows_real_letters_not_a_question_mark_fallback_or_boxes() -> None:
    pdf = one_line(TAMIL)
    fallback = one_line("?" * len(TAMIL))  # what the old writer printed
    band = (MARGIN - 4, MARGIN + 34)
    real, old = ink(render_page(pdf), *band), ink(render_page(fallback), *band)
    real_box, old_box = ink_box(real), ink_box(old)
    assert real_box and old_box
    assert (real_box[2] - real_box[0]) / SCALE == pytest.approx(
        pdffonts.font_for("tamil").shape(TAMIL).advance * 14 / 1000, rel=0.12
    )
    assert real_box != old_box and ink_pixels(real) > 400  # a line of letters, not a few marks
    # and no glyph in the content stream is the font's "no glyph" box (glyph 0)
    for key, text in (("tamil", TAMIL), ("devanagari", DEVANAGARI)):
        assert pdffonts.font_for(key).shape(text).missing == 0


def test_a_conjunct_is_one_drawn_shape_as_wide_as_the_shaping_says() -> None:
    """க்ஷ and क्ष are conjuncts: the page shows one shaped glyph, as wide as HarfBuzz measured it, not separate letters."""
    for key, word in (("tamil", "க்ஷ"), ("devanagari", "क्ष")):
        shaped = pdffonts.font_for(key).shape(word)
        assert len(shaped.glyphs) == 1 and shaped.missing == 0
        box = ink_box(ink(render_page(one_line(word, size=48)), MARGIN - 4, MARGIN + 90))
        assert box
        assert (box[2] - box[0]) / SCALE == pytest.approx(shaped.advance * 48 / 1000, rel=0.2)


def test_a_right_aligned_cell_ends_at_the_same_place_whatever_the_script_on_its_left() -> None:
    d = Document("t", "f")
    d.row([("Question 1", 0, "l"), ("2 / 5", 72, "r")], size=10)
    d.row([(TAMIL, 0, "l"), ("10 / 10", 72, "r")], size=10)
    d.row([(DEVANAGARI, 0, "l"), ("3 / 5", 72, "r")], size=10)
    img = render_page(d.render(T0))
    right = []
    for k in range(3):  # three rows, 13.5 pt apart
        top = MARGIN + 13.5 * k - 2
        box = ink_box(ink(img, top, top + 13.5, left_pt=300))
        assert box
        right.append(box[2])
    assert max(right) - min(right) <= 2  # pixels (a third of a point at this scale)
    assert pdf_text(d.render(T0)).count(" / ") == 3


def test_long_tamil_and_devanagari_paragraphs_wrap_inside_the_margins_and_lose_nothing() -> None:
    para = (TAMIL + " " + DEVANAGARI + " ") * 12
    d = Document("t", "f")
    d.line("Notes: " + para, "Courier", 9)
    pdf = d.render(T0)
    img = render_page(pdf)
    box = ink_box(ink(img, 0, PAGE_H - 60))
    assert box and box[2] / SCALE <= PAGE_W - MARGIN + 1 and box[0] / SCALE >= MARGIN - 1
    assert len([ln for ln in pdf_text(pdf).splitlines() if "தமிழ்" in ln or "हिन्दी" in ln]) >= 3  # it did wrap
    got = " ".join(pdf_text(pdf).replace("Footer", "").split())
    assert (
        " ".join(("Notes: " + para).split()) in got.replace("f Page 1 of 1", "").strip() + " "
    )  # every word survived the wrapping


def test_a_word_longer_than_a_line_is_broken_between_letters_not_inside_one() -> None:
    word = "கொடு" * 80
    d = Document("t", "f")
    d.line(word, "Courier", 12)
    pdf = d.render(T0)
    lines = [ln for ln in pdf_text(pdf).splitlines() if "கொ" in ln or "டு" in ln]
    assert len(lines) >= 2 and "".join(lines) == word and d.replaced is False
    assert all(not ln.startswith(("ொ", "ு", "ா")) for ln in lines)  # no line starts with a stray vowel sign


def test_another_script_is_still_marked_and_does_not_disturb_the_rest() -> None:
    arabic = "".join(chr(c) for c in (0x645, 0x631, 0x62D, 0x628, 0x627))
    d = Document("t", "f")
    d.line(f"{TAMIL} {arabic} {DEVANAGARI}", "Helvetica", 11, wrap=False)
    text = pdf_text(d.render(T0))
    assert d.replaced is True and "?????" in text and TAMIL in text and DEVANAGARI in text


def test_a_character_the_font_has_no_glyph_for_is_flagged() -> None:
    d = Document("t", "f")
    d.line("ok ஄ ok", "Helvetica", 11, wrap=False)  # unassigned in the Tamil block
    d.render(T0)
    assert d.replaced is True


def test_output_is_deterministic_and_fonts_are_embedded_only_when_used() -> None:
    def make(text: str) -> bytes:
        d = Document("t", "f")
        d.line(text, "Helvetica", 11, wrap=False)
        return d.render(T0)

    assert make(TAMIL) == make(TAMIL)
    latin, tamil, both = make("plain"), make(TAMIL), make(TAMIL + DEVANAGARI)
    assert b"FontFile2" not in latin and b"NotoSansTamil" not in latin
    assert b"NotoSansTamil" in tamil and b"NotoSansDevanagari" not in tamil
    assert b"NotoSansTamil" in both and b"NotoSansDevanagari" in both
    assert len(latin) < 3000 and len(tamil) < 150_000 and len(both) < 400_000  # whole fonts, compressed: a sheet stays small


def test_page_breaks_with_indic_content_keep_every_page_numbered_and_readable() -> None:
    d = Document("t", f"Footer {TAMIL}")
    for i in range(250):
        d.row([(f"{i:03d} {TAMIL}", 0, "l"), ("1 / 2", 72, "r")])
    pdf = d.render(T0)
    pages = pdf_pages(pdf)
    assert len(pages) >= 3 and not d.replaced
    for i, page in enumerate(pages, start=1):
        flat = " ".join(page.split())  # (pdfium may start a new line inside a long row)
        assert f"Page {i} of {len(pages)}" in flat and TAMIL in flat
    assert "000 " + TAMIL in " ".join(pages[0].split()) and "249 " + TAMIL in " ".join(pages[-1].split())


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.text(alphabet=st.characters(min_codepoint=0x0900, max_codepoint=0x0DFF), min_size=1, max_size=60), max_size=12)
)
def test_any_text_in_these_scripts_gives_a_pdf_a_reader_accepts(lines: list[str]) -> None:
    d = Document("t", "f")
    for ln in lines:
        d.line(ln)
        d.line(ln, "Helvetica", 10, wrap=False)
        d.row([(ln, 0, "l"), ("1", 72, "r")])
    pdf = d.render(T0)
    pages = pdf_pages(pdf)
    assert len(pages) >= 1 and f"Page {len(pages)} of {len(pages)}" in pages[-1]
