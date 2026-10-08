"""Phase 0b (D8) page characterisation + preprocessing variants. Deterministic, versioned. Run with the engine_b venv.

Variants (version 0.1.1; `st010` = show_through.py v0.1.0 as already run: masked background-normalised gray):
  st011b     binarised: kept front-ink components -> black on white.
  st011g     background-normalised gray everywhere; only REJECTED (low-contrast) components are blanked to white.
  st011g_rl  st011g + morphological removal of ruled lines (solid and dotted) and the vertical margin line.

Features per page (stored in <dst_root>/features.json): binarisation (extreme-pixel share, grey levels used),
show-through estimate (rejected / candidate ink area), ruled-line count and spacing, JPEG blockiness, resolution.

Usage: python spike/preprocess/variants.py --src <pages_dir> --dst-root <dir>   -> <dir>/<variant>/pages/*.jpg
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

VERSION = "0.1.2"  # 0.1.2: ruled-line count via row-coverage profile (0.1.1 counted fragments)
P = {"bg_blur": 61, "thr_block": 41, "thr_c": 12, "keep_ratio": 0.45, "min_area": 12, "dilate": 1,
     "rl_h_frac": 0.08, "rl_v_frac": 0.10, "rl_close": 9}


def _analyse(gray: np.ndarray, p: dict = P):
    bg = cv2.medianBlur(gray, p["bg_blur"])
    norm = cv2.divide(gray, bg, scale=255)
    dark = 255 - norm
    mask = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, p["thr_block"], p["thr_c"])
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    area = stats[:, cv2.CC_STAT_AREA]
    means = np.bincount(labels.ravel(), weights=dark.ravel(), minlength=n) / np.maximum(area, 1)
    p99 = float(np.percentile(dark[mask > 0], 99)) if (mask > 0).any() else 0.0
    keep = (means >= p["keep_ratio"] * p99) & (area >= p["min_area"])
    keep[0] = False
    reject = ~keep
    reject[0] = False
    return norm, mask, labels, area, keep, reject


def ruled_line_mask(mask: np.ndarray, p: dict = P) -> np.ndarray:
    h, w = mask.shape
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (p["rl_close"], 1)))
    horiz = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(w * p["rl_h_frac"]), 1)))
    vert = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(h * p["rl_v_frac"]))))
    return cv2.bitwise_or(horiz, vert)


def count_ruled_lines(mask: np.ndarray, p: dict = P, min_cov: float = 0.15, min_gap: int = 20) -> tuple[int, float | None]:
    """Ruled lines = bands of rows whose (vertically dilated) horizontal-line mask covers > min_cov of the width."""
    h, w = mask.shape
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (p["rl_close"], 1)))
    # Tuned on 6 pages against a visual count (sheet_001 booklet: 25 ruled lines/page); curved WhatsApp pages undercount.
    horiz = cv2.morphologyEx(closed, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (int(w * 0.05), 1)))
    horiz = cv2.dilate(horiz, cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25)))
    cov = (horiz > 0).sum(axis=1) / w
    rows = np.where(cov > min_cov)[0]
    if rows.size == 0:
        return 0, None
    groups, start, prev = [], rows[0], rows[0]
    for r in rows[1:]:
        if r - prev > min_gap:
            groups.append((start + prev) / 2)
            start = r
        prev = r
    groups.append((start + prev) / 2)
    gaps = np.diff(groups)
    return len(groups), (float(np.median(gaps)) if gaps.size else None)


def features(bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    hist = np.bincount(gray.ravel(), minlength=256) / gray.size
    norm, mask, labels, area, keep, reject = _analyse(gray)
    cand = float(area[1:].sum()) or 1.0
    n_ruled, spacing = count_ruled_lines(mask)
    g = gray.astype(np.float64)
    col_d = np.abs(np.diff(g, axis=1))
    boundary = col_d[:, 7::8].mean()
    inside = np.delete(col_d, np.s_[7::8], axis=1).mean()
    return {
        "width": w, "height": h, "megapixels": round(w * h / 1e6, 2),
        "extreme_pixel_share": round(float(hist[:31].sum() + hist[225:].sum()), 4),
        "grey_levels_used": int((hist >= 0.001).sum()),
        "show_through_est": round(float(area[reject].sum()) / cand, 4),
        "ruled_lines": n_ruled,
        "ruled_spacing_px": spacing,
        "jpeg_blockiness": round(float(boundary / inside), 3) if inside else None,
    }


def make_variants(bgr: np.ndarray, p: dict = P) -> dict[str, np.ndarray]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    norm, mask, labels, area, keep, reject = _analyse(gray, p)
    k = np.ones((3, 3), np.uint8)
    kept = cv2.dilate(keep[labels].astype(np.uint8) * 255, k, iterations=p["dilate"])
    rej = cv2.dilate(reject[labels].astype(np.uint8) * 255, k, iterations=p["dilate"])
    rej[kept > 0] = 0  # never blank a pixel that also belongs to front ink
    b = np.where(keep[labels], 0, 255).astype(np.uint8)
    g = norm.copy()
    g[rej > 0] = 255
    g_rl = g.copy()
    g_rl[ruled_line_mask(mask, p) > 0] = 255
    return {"st011b": b, "st011g": g, "st011g_rl": g_rl}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst-root", required=True)
    ap.add_argument("--features-only", help="write features JSON to this path; no variant images (cheap recompute)")
    args = ap.parse_args()
    if args.features_only:
        feats = {"version": VERSION, "params": P, "pages": {f.stem: features(cv2.imread(str(f), cv2.IMREAD_COLOR))
                                                             for f in sorted(Path(args.src).glob("*.jpg"))}}
        Path(args.features_only).write_text(json.dumps(feats, indent=1))
        return 0
    root = Path(args.dst_root)
    feats = {"version": VERSION, "params": P, "pages": {}}
    for f in sorted(Path(args.src).glob("*.jpg")):
        bgr = cv2.imread(str(f), cv2.IMREAD_COLOR)
        feats["pages"][f.stem] = features(bgr)
        for name, img in make_variants(bgr).items():
            out = root / name / "pages"
            out.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out / f.name), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
        print(f.name, feats["pages"][f.stem], flush=True)
    (root / "features.json").write_text(json.dumps(feats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
