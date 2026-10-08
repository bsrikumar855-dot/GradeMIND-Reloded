"""Phase 0 spike: run PaddleOCR (Engine B) page-by-page. Records line text, per-line
recognition confidence, line boxes (pixel coords of the input image), latency."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import paddle
import paddleocr
from paddleocr import PaddleOCR


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cpu", help="cpu (default: shared-GPU plan) or gpu:0 for comparison")
    ap.add_argument("--rec-model", default=None, help="override text_recognition_model_name")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    kw = dict(ocr_version="PP-OCRv5", lang="en", device=args.device,
              use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
    if args.rec_model:
        kw["text_recognition_model_name"] = args.rec_model
    t0 = time.perf_counter()
    ocr = PaddleOCR(**kw)
    load_s = time.perf_counter() - t0

    for page in args.pages:
        t = time.perf_counter()
        results = ocr.predict(page)
        latency = time.perf_counter() - t
        res = results[0].json["res"]
        lines = [
            {"text": txt, "score": round(float(sc), 4), "poly": [[int(x), int(y)] for x, y in poly],
             "box": [int(v) for v in box]}
            for txt, sc, poly, box in zip(res["rec_texts"], res["rec_scores"], res["rec_polys"], res["rec_boxes"])
        ]
        record = {
            "engine": "paddleocr", "paddleocr_version": paddleocr.__version__, "paddle_version": paddle.__version__,
            "config": {k: v for k, v in kw.items()}, "model_settings": res.get("model_settings"),
            "page": Path(page).name, "latency_s": round(latency, 3), "model_load_s": round(load_s, 2),
            "n_lines": len(lines), "lines": lines,
        }
        (out_dir / f"{Path(page).stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        print(f"{Path(page).name}: {latency:.2f}s lines={len(lines)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
