"""Which machine-read lines belong to an answer region, and in what order (3.2; D28). Pure geometry: no I/O, no database.

Display-only: nothing in the grading path may import this module (import-linter contract).

Spaces: an examiner's answer region is [x0, y0, x1, y1] as FRACTIONS of the unrotated page (what the API stores). OCR lines are
in PIXELS of the page image (a polygon plus an axis-aligned box). The viewer's rotate / zoom / pan are view transforms only,
so they never change either space: the same region still selects the same lines when the page is displayed rotated.

A line belongs to a region when at least `min_overlap` of the LINE's own area lies inside the region. The exact intersection
of the line's polygon with the rectangle is computed (Sutherland-Hodgman), so skewed or sideways (rotated) lines are judged
by their real shape, not by the larger axis-aligned box around them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

Point = tuple[float, float]
Rect = tuple[float, float, float, float]  # x0, y0, x1, y1

DEFAULT_MIN_OVERLAP = 0.5


@dataclass(frozen=True)
class LineGeom:
    key: str  # any stable id (the OcrLine id)
    box: Rect  # axis-aligned, page pixels
    poly: Sequence[Point]  # the line's outline, page pixels (may be skewed or rotated)


@dataclass(frozen=True)
class Placed:
    line: LineGeom
    overlap: float  # fraction of the line's area inside the region, 0..1


def polygon_area(poly: Sequence[Point]) -> float:
    n = len(poly)
    if n < 3:
        return 0.0
    s = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
    return abs(s) / 2.0


def _clip(poly: list[Point], inside: tuple[str, float]) -> list[Point]:
    """Clip a polygon against one half-plane of an axis-aligned rectangle: ('x>=', v), ('x<=', v), ('y>=', v), ('y<=', v)."""
    axis, op = inside[0][0], inside[0][1:]
    v = inside[1]
    idx = 0 if axis == "x" else 1

    def inside_pt(p: Point) -> bool:
        return p[idx] >= v if op == ">=" else p[idx] <= v

    def cut(a: Point, b: Point) -> Point:
        t = (v - a[idx]) / (b[idx] - a[idx])
        return (v, a[1] + t * (b[1] - a[1])) if idx == 0 else (a[0] + t * (b[0] - a[0]), v)

    out: list[Point] = []
    for i, cur in enumerate(poly):
        prev = poly[i - 1]
        if inside_pt(cur):
            if not inside_pt(prev):
                out.append(cut(prev, cur))
            out.append(cur)
        elif inside_pt(prev):
            out.append(cut(prev, cur))
    return out


def clip_to_rect(poly: Sequence[Point], rect: Rect) -> list[Point]:
    x0, y0, x1, y1 = rect
    out = list(poly)
    for edge in (("x>=", x0), ("x<=", x1), ("y>=", y0), ("y<=", y1)):
        if not out:
            break
        out = _clip(out, edge)
    return out


def _outline(line: LineGeom) -> Sequence[Point]:
    if len(line.poly) >= 3 and polygon_area(line.poly) > 0:
        return line.poly
    x0, y0, x1, y1 = line.box
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def overlap_fraction(line: LineGeom, rect: Rect) -> float:
    """Share (0..1) of the line's area that lies inside `rect`. A line with no area at all (a zero-height box) counts as inside
    when its centre is inside the rectangle."""
    outline = _outline(line)
    area = polygon_area(outline)
    if area <= 0:
        x0, y0, x1, y1 = line.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        return 1.0 if rect[0] <= cx <= rect[2] and rect[1] <= cy <= rect[3] else 0.0
    return min(1.0, polygon_area(clip_to_rect(outline, rect)) / area)


def reading_order(lines: Sequence[LineGeom]) -> list[LineGeom]:
    """Top to bottom, and left to right within a row. Rows are found by clustering the lines' vertical CENTRES: a line joins the
    current row when its centre is within half of the median line height of the row's mean centre. (Comparing against the row's
    growing span instead would let one tall line, e.g. a margin note, swallow its neighbours and scramble the order.)
    Deterministic: ties break on x then key, so the result does not depend on the input order."""
    if not lines:
        return []
    heights = sorted(ln.box[3] - ln.box[1] for ln in lines)
    tol = 0.5 * max(heights[len(heights) // 2], 1.0)

    def cy(ln: LineGeom) -> float:
        return (ln.box[1] + ln.box[3]) / 2

    rows: list[list[LineGeom]] = []
    sums: list[float] = []
    for ln in sorted(lines, key=lambda ln: (cy(ln), ln.box[0], ln.key)):
        if rows and abs(cy(ln) - sums[-1] / len(rows[-1])) <= tol:
            rows[-1].append(ln)
            sums[-1] += cy(ln)
        else:
            rows.append([ln])
            sums.append(cy(ln))
    out: list[LineGeom] = []
    for row in rows:
        out.extend(sorted(row, key=lambda ln: (ln.box[0], ln.box[1], ln.key)))
    return out


def region_rect(region: Rect, page_w: float, page_h: float) -> Rect:
    """A region in page fractions -> page pixels."""
    return (region[0] * page_w, region[1] * page_h, region[2] * page_w, region[3] * page_h)


def lines_in_region(
    lines: Sequence[LineGeom], region: Rect, page_w: float, page_h: float, min_overlap: float = DEFAULT_MIN_OVERLAP
) -> list[Placed]:
    """The lines whose own area lies at least `min_overlap` inside the region, in reading order. An empty region (or a page
    without lines) gives an empty list."""
    if not lines:
        return []
    rect = region_rect(region, page_w, page_h)
    hits = {ln.key: ov for ln in lines if (ov := overlap_fraction(ln, rect)) >= min_overlap and ov > 0}
    by_key = {ln.key: ln for ln in lines}
    return [Placed(by_key[ln.key], hits[ln.key]) for ln in reading_order([by_key[k] for k in hits])]
