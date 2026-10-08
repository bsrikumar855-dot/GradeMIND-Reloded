"""Redaction for committing sample data (owner decision D6).

Spec (data/redaction.json): per sheet, pages redacted in full (cover pages) and optional per-page boxes.
  --images : move originals data/samples/<sheet>/{pages,source.pdf} -> data/originals/<sheet>/ (gitignored, once),
             then write redacted copies to data/samples/<sheet>/pages and redact preprocessed copies in spike/pages/.
  --json   : in every run output / fixture JSON for a fully-redacted page, replace all text with [REDACTED] and drop
             token ids / logprobs (token ids decode back to the text).
  --verify : grep committed paths for identifier patterns given via --pattern-file (kept LOCAL: it lists the
             identifiers themselves); non-zero exit on any hit.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "data" / "redaction.json"
R = "[REDACTED]"
TEXT_KEYS = {"text", "text_raw", "b_text", "raw", "raw_marker"}
DROP_KEYS = {"token_ids", "token_logprobs", "token_margins"}


def redact_image(src: Path, dst: Path, full: bool, boxes: list[list[int]]) -> None:
    im = Image.open(src).convert("RGB")
    d = ImageDraw.Draw(im)
    if full:
        d.rectangle([0, 0, im.width, im.height], fill=(0, 0, 0))
    for b in boxes:
        d.rectangle(b, fill=(0, 0, 0))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=92)


def scrub(obj):
    if isinstance(obj, dict):
        return {k: (R if k in TEXT_KEYS and isinstance(v, str) else [] if k in DROP_KEYS else scrub(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--pattern-file", help="local file, one regex per line (identifiers); never committed")
    args = ap.parse_args()
    spec = json.loads(SPEC.read_text())
    full = {(s, p) for s, cfg in spec["sheets"].items() for p in cfg["full_pages"]}

    if args.images:
        for sheet, cfg in spec["sheets"].items():
            orig = ROOT / "data" / "originals" / sheet
            live = ROOT / "data" / "samples" / sheet
            if not orig.exists():
                orig.mkdir(parents=True)
                shutil.move(str(live / "pages"), str(orig / "pages"))
                if (live / "source.pdf").exists():
                    shutil.move(str(live / "source.pdf"), str(orig / "source.pdf"))
            for img in sorted((orig / "pages").glob("*.jpg")):
                redact_image(img, live / "pages" / img.name, img.stem in cfg["full_pages"], cfg.get("boxes", {}).get(img.stem, []))
            for pre in (ROOT / "spike" / "pages").glob(f"{sheet}_*/pages"):
                for img in sorted(pre.glob("*.jpg")):
                    if img.stem in cfg["full_pages"] or img.stem in cfg.get("boxes", {}):
                        redact_image(img, img, img.stem in cfg["full_pages"], cfg.get("boxes", {}).get(img.stem, []))
            print(f"images redacted: {sheet}")

    if args.json:
        n = 0
        roots = [ROOT / "spike" / "runs", ROOT / "data" / "fixtures"]
        for base in roots:
            for f in base.rglob("*.json"):
                parts = f.parts
                sheet = next((p for p in parts if p.startswith("sheet_")), None)
                if sheet and (sheet.split("_pad")[0].split("_st")[0], f.stem) in full:
                    f.write_text(json.dumps(scrub(json.loads(f.read_text())), ensure_ascii=False, indent=1))
                    n += 1
        print(f"json records redacted: {n}")

    if args.verify:
        pats = [re.compile(l.strip(), re.I) for l in Path(args.pattern_file).read_text().splitlines() if l.strip()]
        hits = []
        for base in [ROOT / "spike" / "runs", ROOT / "data" / "fixtures", ROOT / "data" / "transcriptions", ROOT / "docs"]:
            for f in base.rglob("*"):
                if f.is_file() and f.suffix in {".json", ".txt", ".md", ".log"}:
                    t = f.read_text(errors="ignore")
                    hits += [f"{f.relative_to(ROOT)}: {p.pattern}" for p in pats if p.search(t)]
        print(f"verify: {len(hits)} hits")
        for h in hits[:20]:
            print("  ", h)
        return 1 if hits else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
