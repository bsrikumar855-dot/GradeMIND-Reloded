"""Phase 0b (D1b) Engine C: line-level handwriting recognition on Engine B's detected line boxes.

For each page: read Engine B's JSON for the SAME page image (so boxes align), crop each detected line
(axis-aligned box + padding), recognise with TrOCR (greedy, deterministic, fp32), and write the same
"lines" JSON shape as Engine B, so score.py/summarize.py read it unchanged.

Line crops give the decoder only one line of context, which should limit page-level language-prior
"autocorrection". TrOCR's decoder is still autoregressive with a text prior; whether it autocorrects is measured, not assumed.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

PAD = 6


def undo_iam_spacing(text: str) -> str:
    """IAM ground truth (TrOCR's fine-tuning data) writes punctuation space-separated ('year .'). Undo that format
    convention only: no characters are added or removed. The raw decode is kept alongside."""
    text = re.sub(r"\s+([.,;:!?)\]'])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--b-dir", required=True, help="Engine B output dir for these exact page images")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    processor = TrOCRProcessor.from_pretrained(args.model)
    model = VisionEncoderDecoderModel.from_pretrained(args.model, dtype=torch.float32).eval().cuda()
    load_s = time.perf_counter() - t0

    for page in args.pages:
        b = json.loads((Path(args.b_dir) / f"{Path(page).stem}.json").read_text())
        img = Image.open(page).convert("RGB")
        W, H = img.size
        torch.cuda.reset_peak_memory_stats()
        t = time.perf_counter()
        out_lines = []
        lines = b["lines"]
        for i in range(0, len(lines), args.batch):
            chunk = lines[i:i + args.batch]
            crops = []
            for ln in chunk:
                x0, y0, x1, y1 = ln["box"]
                crops.append(img.crop((max(0, x0 - PAD), max(0, y0 - PAD), min(W, x1 + PAD), min(H, y1 + PAD))))
            pv = processor(images=crops, return_tensors="pt").pixel_values.cuda()
            with torch.no_grad():
                gen = model.generate(pv, num_beams=1, do_sample=False, max_new_tokens=args.max_new_tokens,
                                     output_scores=True, return_dict_in_generate=True)
            trans = model.compute_transition_scores(gen.sequences, gen.scores, normalize_logits=True)
            texts = processor.batch_decode(gen.sequences, skip_special_tokens=True)
            for k, (ln, txt) in enumerate(zip(chunk, texts)):
                lp = trans[k]
                ids = gen.sequences[k, 1:]
                valid = ids != processor.tokenizer.pad_token_id
                probs = torch.exp(lp[valid]).tolist()
                out_lines.append({
                    "text": undo_iam_spacing(txt), "text_raw": txt, "score": round(sum(probs) / len(probs), 4) if probs else 0.0,
                    "min_token_prob": round(min(probs), 4) if probs else 0.0,
                    "box": ln["box"], "poly": ln["poly"], "b_text": ln["text"], "b_score": ln["score"],
                })
        torch.cuda.synchronize()
        latency = time.perf_counter() - t
        record = {
            "engine": "trocr-line", "model_revision": args.revision, "detector": "engine_b (PP-OCRv5_server_det)",
            "postprocess": "undo_iam_spacing (format only)", "decoding": {"num_beams": 1, "do_sample": False, "max_new_tokens": args.max_new_tokens, "dtype": "fp32"},
            "crop_pad_px": PAD, "page": Path(page).name, "latency_s": round(latency, 3), "model_load_s": round(load_s, 2),
            "n_lines": len(out_lines), "lines": out_lines,
            "peak_torch_alloc_mib": round(torch.cuda.max_memory_allocated() / 2**20),
            "peak_rss_mib_process": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        }
        (out_dir / f"{Path(page).stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        print(f"{Path(page).name}: {latency:.1f}s lines={len(out_lines)} peak_vram={record['peak_torch_alloc_mib']}MiB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
