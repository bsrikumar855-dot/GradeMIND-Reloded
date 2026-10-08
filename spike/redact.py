"""Redaction for committing sample data (owner decisions D6, D15).

Originals stay where runs read them (data/samples/<sheet>/pages, gitignored) and are never modified.
  --images        write redacted copies of ORIGINAL page images to data/redacted/<sheet>/pages/ (committed via LFS).
                  Preprocessing-variant images are NOT exported (reproducible from code + config).
  --export RUN..  copy text/JSON/log outputs of the given run dirs to data/run_outputs/<run_id>/ (code/ snapshots and
                  images skipped); records of fully-redacted pages get all text -> [REDACTED], token ids/logprobs dropped.
  --fixtures      redact data/fixtures in place (real-page fixtures; none are cover pages today, still enforced).
  --verify        grep every committed data path for identifier patterns (--pattern-file, kept LOCAL); exit 1 on any hit.
Spec: data/redaction.json (cover pages = full black box; optional per-page boxes).
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
EXPORT_SUFFIXES = {".json", ".txt", ".log", ".md"}
COMMITTED = ["data/redacted", "data/run_outputs", "data/transcriptions", "data/fixtures", "docs", "spike"]


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
        return {k: (R if k in TEXT_KEYS and isinstance(v, str) else [] if k in DROP_KEYS else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    return obj


def collapse_log(src: Path, dst: Path) -> None:
    out, prev, rep = [], None, 0
    with open(src, errors="replace") as f:
        for line in f:
            if line == prev:
                rep += 1
                continue
            if rep:
                out.append(f"[previous line repeated {rep} times]\n")
            out.append(line)
            prev, rep = line, 0
    if rep:
        out.append(f"[previous line repeated {rep} times]\n")
    dst.write_text("".join(out))


def is_full(full: set, path: Path) -> bool:
    sheet = next((p for p in path.parts if p.startswith("sheet_")), None)
    return bool(sheet) and (re.sub(r"_(pad|st|v0).*$", "", sheet), path.stem) in full


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images", action="store_true")
    ap.add_argument("--export", nargs="*", default=[])
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--pattern-file")
    args = ap.parse_args()
    spec = json.loads(SPEC.read_text())
    full = {(s, p) for s, cfg in spec["sheets"].items() for p in cfg["full_pages"]}

    if args.images:
        for sheet, cfg in spec["sheets"].items():
            for img in sorted((ROOT / "data" / "samples" / sheet / "pages").glob("*.jpg")):
                redact_image(img, ROOT / "data" / "redacted" / sheet / "pages" / img.name,
                             img.stem in cfg["full_pages"], cfg.get("boxes", {}).get(img.stem, []))
            print(f"redacted images -> data/redacted/{sheet}/pages")

    for run in map(Path, args.export):
        run = run.resolve()
        dst_root = ROOT / "data" / "run_outputs" / run.name
        n = red = 0
        collapsed: list[str] = []
        for f in run.rglob("*"):
            rel = f.relative_to(run)
            if not f.is_file() or rel.parts[0] == "code" or f.suffix not in EXPORT_SUFFIXES:
                continue
            dst = dst_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if f.suffix == ".log" and f.stat().st_size > 1_000_000:
                collapse_log(f, dst)
                collapsed.append(str(rel))
            elif f.suffix == ".json" and is_full(full, f):
                dst.write_text(json.dumps(scrub(json.loads(f.read_text())), ensure_ascii=False, indent=1))
                red += 1
            else:
                shutil.copy2(f, dst)
            n += 1
        (dst_root / "EXPORT_NOTE.json").write_text(json.dumps({
            "source_run": run.name, "redaction": "data/redaction.json via spike/redact.py",
            "logs_collapsed": collapsed,
            "collapse_rule": "logs > 1 MB: runs of identical consecutive lines collapsed to one line + '[previous line repeated N times]'; raw logs kept local"}, indent=1))
        print(f"exported {run.name}: {n} files ({red} redacted records, {len(collapsed)} logs collapsed)")

    if args.fixtures:
        n = 0
        for f in (ROOT / "data" / "fixtures").rglob("*.json"):
            if is_full(full, f):
                f.write_text(json.dumps(scrub(json.loads(f.read_text())), ensure_ascii=False, indent=1))
                n += 1
        print(f"fixtures redacted: {n}")

    if args.verify:
        pats = [re.compile(l.strip(), re.I) for l in Path(args.pattern_file).read_text().splitlines() if l.strip()]
        hits, scanned = [], 0
        for base in COMMITTED:
            for f in (ROOT / base).rglob("*"):
                if f.is_file() and f.suffix in EXPORT_SUFFIXES | {".py", ".html", ".sh"} and ".venv" not in f.parts and "runs" not in f.parts[len(ROOT.parts):][:2]:
                    scanned += 1
                    t = f.read_text(errors="ignore")
                    hits += [f"{f.relative_to(ROOT)}: {p.pattern}" for p in pats if p.search(t)]
        print(f"verify: scanned {scanned} files, {len(hits)} hits")
        for h in hits[:20]:
            print("  ", h)
        return 1 if hits else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
