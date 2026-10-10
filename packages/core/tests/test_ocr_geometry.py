"""Pure geometry for machine-read lines vs answer regions (3.2): examples for every case, then Hypothesis properties."""

from __future__ import annotations

import math
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grademind_core.ocr_geometry import (
    LineGeom,
    clip_to_rect,
    lines_in_region,
    overlap_fraction,
    polygon_area,
    reading_order,
    region_rect,
)

W, H = 1000.0, 1400.0


def line(key: str, x0: float, y0: float, x1: float, y1: float) -> LineGeom:
    return LineGeom(key, (x0, y0, x1, y1), [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, abs_tol=tol)


# ------------------------------------------------------------------------------------------------------- primitives


def test_polygon_area() -> None:
    assert near(polygon_area([(0, 0), (10, 0), (10, 10), (0, 10)]), 100)
    assert near(polygon_area([(0, 10), (10, 10), (10, 0), (0, 0)]), 100)  # winding order does not matter
    assert near(polygon_area([(0, 0), (10, 0), (0, 10)]), 50)
    assert polygon_area([(0, 0), (5, 5)]) == 0 and polygon_area([]) == 0


def test_clip_to_rect() -> None:
    sq = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert near(polygon_area(clip_to_rect(sq, (0, 0, 10, 10))), 100)  # fully inside
    assert near(polygon_area(clip_to_rect(sq, (5, 0, 20, 10))), 50)  # half
    assert clip_to_rect(sq, (20, 20, 30, 30)) == []  # outside
    assert near(polygon_area(clip_to_rect(sq, (-5, -5, 50, 50))), 100)  # polygon inside a bigger rect
    assert near(polygon_area(clip_to_rect(sq, (2, 2, 4, 4))), 4)  # rect inside the polygon
    assert near(polygon_area(clip_to_rect(sq, (10, 0, 20, 10))), 0)  # touching along an edge only
    tri = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]
    assert near(polygon_area(clip_to_rect(tri, (0, 0, 5, 10))), 37.5)  # a non-axis-aligned edge is cut exactly


# --------------------------------------------------------------------------------------------------- overlap and threshold


def test_overlap_fraction_and_the_threshold_boundary() -> None:
    ln = line("a", 0, 0, 100, 20)
    assert near(overlap_fraction(ln, (50, 0, 200, 20)), 0.5)
    assert near(overlap_fraction(ln, (0, 0, 100, 20)), 1.0)
    assert overlap_fraction(ln, (300, 300, 400, 400)) == 0.0
    # exactly at the threshold is IN; just below is OUT
    region = (0.05, 0.0, 1.0, 1.0)  # starts at x = 50 on a 1000-wide page -> the 0..100 line has exactly half inside
    assert [p.line.key for p in lines_in_region([line("a", 0, 100, 100, 120)], region, W, H)] == ["a"]
    assert lines_in_region([line("a", 0, 100, 100, 120)], (0.0501, 0.0, 1.0, 1.0), W, H) == []
    assert lines_in_region([line("a", 0, 100, 100, 120)], (0.0501, 0.0, 1.0, 1.0), W, H, min_overlap=0.4)


def test_a_skewed_line_is_judged_by_its_real_shape_not_its_bounding_box() -> None:
    # a thin diagonal stroke (slope 1, thickness 20): area 2000; its axis-aligned box is 100 x 120
    skew = LineGeom("s", (0, 0, 100, 120), [(0, 0), (100, 100), (100, 120), (0, 20)])
    rect = (0.0, 0.0, 200.0, 30.0)  # the top strip, y <= 30
    assert near(overlap_fraction(skew, rect), 0.2)  # polygon: 400 of 2000
    box_only = LineGeom("b", (0, 0, 100, 120), [])
    assert near(overlap_fraction(box_only, rect), 0.25)  # a box would say 0.25
    region = (0.0, 0.0, 0.2, 30 / H)  # in page fractions: x <= 200, y <= 30
    assert lines_in_region([skew], region, W, H, min_overlap=0.22) == []  # real shape: 0.2 < 0.22, excluded
    assert [p.line.key for p in lines_in_region([box_only], region, W, H, min_overlap=0.22)] == ["b"]  # a box-only line: 0.25


def test_sideways_page_vertical_lines() -> None:
    """A page scanned on its side: the lines are tall and thin. They are placed by the same geometry, in a stable order."""
    cols = [line(f"c{i}", 100 + 60 * i, 50, 130 + 60 * i, 900) for i in range(3)]
    got = lines_in_region(cols, (0.0, 0.0, 0.5, 0.8), W, H)  # x <= 500, y <= 1120: all three lie fully inside
    assert [p.line.key for p in got] == ["c0", "c1", "c2"]
    top_only = lines_in_region(cols, (0.0, 0.0, 0.5, 0.05), W, H, min_overlap=0.02)  # y <= 70: 20 of 850 px inside
    assert [(p.line.key, round(p.overlap, 3)) for p in top_only] == [("c0", 0.024), ("c1", 0.024), ("c2", 0.024)]
    assert lines_in_region(cols, (0.0, 0.0, 0.5, 0.05), W, H) == []  # not enough of any line


def test_zero_area_lines_and_degenerate_outlines() -> None:
    flat = LineGeom("f", (10, 10, 50, 10), [(10, 10), (50, 10), (50, 10)])  # zero height
    assert overlap_fraction(flat, (0, 0, 100, 100)) == 1.0 and overlap_fraction(flat, (60, 0, 100, 100)) == 0.0
    two_points = LineGeom("t", (0, 0, 40, 10), [(0, 0), (40, 10)])  # fewer than 3 points: falls back to the box
    assert near(overlap_fraction(two_points, (0, 0, 20, 10)), 0.5)


def test_empty_regions_and_empty_pages() -> None:
    assert lines_in_region([], (0.1, 0.1, 0.9, 0.9), W, H) == []  # a page with no machine text
    assert lines_in_region([line("a", 100, 100, 300, 120)], (0.5, 0.5, 0.5, 0.5), W, H) == []  # zero-size region
    assert lines_in_region([line("a", 100, 100, 300, 120)], (0.6, 0.6, 0.9, 0.9), W, H) == []  # nothing inside
    assert region_rect((0.1, 0.2, 0.5, 0.6), 1000, 2000) == (100.0, 400.0, 500.0, 1200.0)


# ------------------------------------------------------------------------------------------------------- reading order


def test_reading_order_rows_then_columns() -> None:
    row1 = [line("r1c", 600, 100, 900, 130), line("r1a", 50, 102, 200, 128), line("r1b", 300, 98, 500, 131)]
    row2 = [line("r2b", 400, 200, 700, 230), line("r2a", 50, 198, 300, 232)]
    expected = ["r1a", "r1b", "r1c", "r2a", "r2b"]
    for seed in range(5):
        shuffled = (row1 + row2)[:]
        random.Random(seed).shuffle(shuffled)  # noqa: S311 - seeded shuffle in a test
        assert [ln.key for ln in reading_order(shuffled)] == expected  # independent of the input order


def test_row_grouping_tolerance_is_half_the_median_line_height() -> None:
    a = line("a", 0, 100, 100, 140)  # centre 120; all lines are 40 high, so the tolerance is 20
    on_the_edge = line("b", 200, 120, 300, 160)  # centre 140: exactly 20 away -> same row, so ordered by x
    assert [ln.key for ln in reading_order([on_the_edge, a])] == ["a", "b"]
    just_out = line("b", 200, 120.2, 300, 160.2)  # centre 140.2 -> a new row
    below = line(
        "c", 5, 150, 105, 190
    )  # centre 170: far from the row's mean (130) -> its own row, although it starts further left
    assert [ln.key for ln in reading_order([below, on_the_edge, a])] == ["a", "b", "c"]
    assert [ln.key for ln in reading_order([just_out, a])] == ["a", "b"]  # separate rows, still top to bottom


def test_one_tall_line_does_not_swallow_its_neighbours() -> None:
    a, tall, d = line("a", 0, 100, 100, 140), line("t", 400, 90, 450, 250), line("d", 10, 200, 60, 240)
    assert [ln.key for ln in reading_order([tall, d, a])] == ["a", "t", "d"]  # top to bottom by centre, not merged into one row


def test_lines_in_region_returns_overlap_and_order() -> None:
    lines = [line("b", 100, 300, 800, 330), line("a", 100, 100, 800, 130), line("out", 100, 900, 800, 930)]
    got = lines_in_region(lines, (0.05, 0.05, 0.95, 0.30), W, H)  # y from 70 to 420
    assert [(p.line.key, p.overlap) for p in got] == [("a", 1.0), ("b", 1.0)]


# ------------------------------------------------------------------------------------------------------------ properties

PROPS = settings(max_examples=300, deadline=None)

coord = st.floats(min_value=0, max_value=1000, allow_nan=False, allow_infinity=False)


@st.composite
def lines_strategy(draw: st.DrawFn) -> list[LineGeom]:
    n = draw(st.integers(0, 12))
    out = []
    for i in range(n):
        x0, y0 = draw(st.floats(0, 900)), draw(st.floats(0, 1300))
        w, h = draw(st.floats(5, 100)), draw(st.floats(5, 60))
        shear = draw(st.floats(-40, 40))
        poly = [(x0, y0), (x0 + w, y0 + shear), (x0 + w, y0 + shear + h), (x0, y0 + h)]
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        out.append(LineGeom(f"k{i}", (min(xs), min(ys), max(xs), max(ys)), poly))
    return out


@st.composite
def rects(draw: st.DrawFn) -> tuple[float, float, float, float]:
    x0, y0 = draw(st.floats(0, 900)), draw(st.floats(0, 1300))
    return (x0, y0, x0 + draw(st.floats(1, 600)), y0 + draw(st.floats(1, 800)))


@PROPS
@given(lines_strategy(), rects())
def test_property_overlap_is_a_fraction(lines: list[LineGeom], rect: tuple[float, float, float, float]) -> None:
    for ln in lines:
        assert 0.0 <= overlap_fraction(ln, rect) <= 1.0


@PROPS
@given(lines_strategy(), st.floats(0, 1000), st.floats(0, 1400))
def test_property_overlap_is_additive_across_a_split(lines: list[LineGeom], cut_x: float, cut_y: float) -> None:
    """Splitting the page into two rectangles shares each line's area between them exactly."""
    big = (-1e6, -1e6, 1e6, 1e6)
    for ln in lines:
        left, right = overlap_fraction(ln, (-1e6, -1e6, cut_x, 1e6)), overlap_fraction(ln, (cut_x, -1e6, 1e6, 1e6))
        top, bottom = overlap_fraction(ln, (-1e6, -1e6, 1e6, cut_y)), overlap_fraction(ln, (-1e6, cut_y, 1e6, 1e6))
        assert near(left + right, 1.0, 1e-6) and near(top + bottom, 1.0, 1e-6) and near(overlap_fraction(ln, big), 1.0, 1e-6)


@PROPS
@given(lines_strategy(), rects(), st.floats(0, 300), st.randoms(use_true_random=False))
def test_property_growing_a_region_never_loses_lines_and_order_ignores_input_order(
    lines: list[LineGeom], rect: tuple[float, float, float, float], grow: float, rnd: random.Random
) -> None:
    def frac(r: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (r[0] / W, r[1] / H, r[2] / W, r[3] / H)

    small = lines_in_region(lines, frac(rect), W, H)
    bigger = (rect[0] - grow, rect[1] - grow, rect[2] + grow, rect[3] + grow)
    assert {p.line.key for p in small} <= {p.line.key for p in lines_in_region(lines, frac(bigger), W, H)}
    shuffled = lines[:]
    rnd.shuffle(shuffled)
    assert [p.line.key for p in lines_in_region(shuffled, frac(rect), W, H)] == [p.line.key for p in small]


@PROPS
@given(lines_strategy())
def test_property_the_whole_page_selects_every_line_inside_it(lines: list[LineGeom]) -> None:
    inside = [ln for ln in lines if ln.box[0] >= 0 and ln.box[2] <= W and ln.box[1] >= 0 and ln.box[3] <= H]
    got = {p.line.key for p in lines_in_region(inside, (0.0, 0.0, 1.0, 1.0), W, H)}
    assert got == {ln.key for ln in inside}


def test_unknown_threshold_values_behave() -> None:
    ln = line("a", 0, 0, 100, 20)
    assert lines_in_region([ln], (0.0, 0.0, 0.05, 1.0), W, H, min_overlap=0.0) != []  # 50 of 100 inside, threshold 0 -> in
    assert pytest.approx(overlap_fraction(ln, (0, 0, 100, 20))) == 1.0
