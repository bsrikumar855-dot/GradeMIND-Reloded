"""Phase 0b (D1c) show-through suppression probe. Deterministic, parameterised, versioned.

Steps:
  1. Background normalisation: gray / median-blurred background -> flattens shadows and paper tint.
  2. Adaptive threshold (Gaussian) on the normalised image -> candidate ink mask.
  3. Low-contrast component filter: connected components whose mean ink darkness is below
     `keep_ratio` x (page's p99 ink darkness) are dropped. Show-through (mirror writing from the reverse
     side), ruled lines and faint watermarks are all lighter than front-side pen strokes.
  4. Output: normalised gray where kept ink (slightly dilated, to retain anti-aliased edges) else white.

Not implemented: explicit mirrored-glyph detection; contrast is used as the proxy.
Run with the engine_b venv (OpenCV 4.10).

Usage: python spike/preprocess/show_through.py --src <pages_dir> --dst <out_pages_dir> [--debug-dir D]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

VERSION = "0.1.0"
PARAMS = {"bg_blur": 61, "thr_block": 41, "thr_c": 12, "keep_ratio": 0.45, "min_area": 12, "edge_dilate": 1}


def suppress(bgr: np.ndarray, p: dict = PARAMS) -> tuple[np.ndarray, dict]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    bg = cv2.medianBlur(gray, p["bg_blur"])
    norm = cv2.divide(gray, bg, scale=255)                       # 1. background normalisation
    dark = 255 - norm                                            # ink darkness (0 = paper)
    mask = cv2.adaptiveThreshold(norm, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
                                 p["thr_block"], p["thr_c"])     # 2. candidate ink
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return np.full_like(norm, 255), {"components": 0, "kept": 0}
    sums = np.bincount(labels.ravel(), weights=dark.ravel(), minlength=n)
    means = sums / np.maximum(stats[:, cv2.CC_STAT_AREA], 1)
    p99 = float(np.percentile(dark[mask > 0], 99))
    keep = (means >= p["keep_ratio"] * p99) & (stats[:, cv2.CC_STAT_AREA] >= p["min_area"])
    keep[0] = False                                              # 3. low-contrast filter
    kept = keep[labels].astype(np.uint8) * 255
    if p["edge_dilate"]:
        kept = cv2.dilate(kept, np.ones((3, 3), np.uint8), iterations=p["edge_dilate"])
    out = np.where(kept > 0, norm, 255).astype(np.uint8)         # 4. compose
    return out, {"components": int(n - 1), "kept": int(keep.sum()), "ink_p99": round(p99, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--debug-dir")
    args = ap.parse_args()
    dst = Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    log = {"version": VERSION, "params": PARAMS, "pages": {}}
    for f in sorted(Path(args.src).glob("*.jpg")):
        bgr = cv2.imread(str(f), cv2.IMREAD_COLOR)
        out, info = suppress(bgr)
        cv2.imwrite(str(dst / f.name), out, [cv2.IMWRITE_JPEG_QUALITY, 95])
        log["pages"][f.name] = info
        print(f.name, info, flush=True)
    (dst.parent / "show_through.json").write_text(json.dumps(log, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
