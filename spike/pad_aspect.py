"""Spike-only preprocessing probe: pad each page to a fixed aspect ratio (default 2:3, width:height) with white,
so Unlimited-OCR's gundam crop grid (aspect-ratio driven, 12..30 crops observed) becomes a constant 4x6=24.
Output goes to spike/pages/ (gitignored). Not the production preprocessing pipeline (spec §6)."""
import argparse
from pathlib import Path
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("--src", required=True)
ap.add_argument("--dst", required=True)
ap.add_argument("--aspect", type=float, default=2 / 3)
a = ap.parse_args()
Path(a.dst).mkdir(parents=True, exist_ok=True)
for f in sorted(Path(a.src).glob("*.jpg")):
    im = Image.open(f).convert("RGB")
    w, h = im.size
    if w / h > a.aspect:
        nw, nh = w, round(w / a.aspect)
    else:
        nw, nh = round(h * a.aspect), h
    canvas = Image.new("RGB", (nw, nh), (255, 255, 255))
    canvas.paste(im, ((nw - w) // 2, (nh - h) // 2))
    canvas.save(Path(a.dst) / f.name, quality=95)
    print(f.name, (w, h), "->", (nw, nh))
