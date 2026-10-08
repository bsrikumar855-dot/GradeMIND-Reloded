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

VERSION = "0.1.1"
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


def features(bgr: np.ndarray) -> dict:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    hist = np.bincount(gray.ravel(), minlength=256) / gray.size
    norm, mask, labels, area, keep, reject = _analyse(gray)
    cand = float(area[1:].sum()) or 1.0
    rl = ruled_line_mask(mask)
    n_rows, row_lbl = cv2.connectedComponents(cv2.morphologyEx(rl, cv2.MORPH_OPEN, np.ones((1, int(w * P["rl_h_frac"])), np.uint8)))
    ys = sorted(int(np.mean(np.where(row_lbl == i)[0])) for i in range(1, n_rows)) if n_rows > 1 else []
    gaps = np.diff(ys) if len(ys) > 1 else np.array([])
    g = gray.astype(np.float64)
    col_d = np.abs(np.diff(g, axis=1))
    boundary = col_d[:, 7::8].mean()
    inside = np.delete(col_d, np.s_[7::8], axis=1).mean()
    return {
        "width": w, "height": h, "megapixels": round(w * h / 1e6, 2),
        "extreme_pixel_share": round(float(hist[:31].sum() + hist[225:].sum()), 4),
        "grey_levels_used": int((hist >= 0.001).sum()),
        "show_through_est": round(float(area[reject].sum()) / cand, 4),
        "ruled_lines": max(0, n_rows - 1),
        "ruled_spacing_px": float(np.median(gaps)) if gaps.size else None,
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
    args = ap.parse_args()
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
