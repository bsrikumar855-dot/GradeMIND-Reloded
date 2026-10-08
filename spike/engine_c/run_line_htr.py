"""Phase 0b Engine C: line-level handwriting recognition on Engine B's detected line polygons.

For each page: read Engine B's JSON for the SAME page image (so boxes align), perspective-warp each detected quad
to an upright crop (D9), gate empty crops (NO_TEXT, TrOCR skipped), recognise the rest with TrOCR (greedy,
deterministic, fp32), and write the same "lines" JSON shape as Engine B, so score.py etc. read it unchanged.

Empty-crop gate: "front ink" pixels are those darker than GATE_KEEP_RATIO x the page's p99 ink darkness (the same
contrast rule as spike/preprocess/show_through.py). A crop with fewer than GATE_MIN_INK_PX front-ink pixels gets NO_TEXT.
Rule 12: the weights file actually loaded is hashed and must equal --weights-sha256; the loaded architecture and dtype are recorded.
"""

from __future__ import annotations

import argparse
import json
import re
import resource
import sys
import time
from pathlib import Path

import numpy as np
import torch
import transformers
from PIL import Image
from transformers import TrOCRProcessor, VisionEncoderDecoderModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from resolved import require_equal, sha256_file  # noqa: E402

PAD = 6
GATE_KEEP_RATIO = 0.45
GATE_MIN_INK_PX = 60
NO_TEXT = "NO_TEXT"


def undo_iam_spacing(text: str) -> str:
    """IAM ground truth (TrOCR's fine-tuning data) writes punctuation space-separated ('year .'). Undo that format
    convention only: no characters are added or removed. The raw decode is kept alongside."""
    text = re.sub(r"\s+([.,;:!?)\]'])", r"\1", text)
    return re.sub(r"([(\[])\s+", r"\1", text).strip()


def order_quad(poly: list[list[int]]) -> np.ndarray:
    """Return points as top-left, top-right, bottom-right, bottom-left."""
    pts = np.array(poly, dtype=np.float64)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]])


def warp_crop(img: Image.Image, poly: list[list[int]], pad: int = PAD) -> Image.Image:
    q = order_quad(poly)
    c = q.mean(0)
    q = q + np.sign(q - c) * pad
    tl, tr, br, bl = q
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    # PIL QUAD source order: upper-left, lower-left, lower-right, upper-right
    return img.transform((max(w, 1), max(h, 1)), Image.QUAD, data=tuple(np.concatenate([tl, bl, br, tr]).tolist()),
                         resample=Image.BICUBIC, fillcolor=(255, 255, 255))


def darkness(gray: np.ndarray) -> np.ndarray:
    bg = float(np.percentile(gray, 90)) or 1.0
    return np.clip(255 - gray.astype(np.float64) / bg * 255, 0, 255)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--weights-sha256", required=True, help="rule 12: expected sha256 of the loaded weights file")
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--b-dir", required=True, help="Engine B output dir for these exact page images")
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-new-tokens", type=int, default=96)
    ap.add_argument("--no-gate", action="store_true", help="disable the empty-crop gate (ablation)")
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    wfile = next(p for p in [Path(args.model) / "model.safetensors", Path(args.model) / "pytorch_model.bin"] if p.exists())
    t0 = time.perf_counter()
    processor = TrOCRProcessor.from_pretrained(args.model)
    model = VisionEncoderDecoderModel.from_pretrained(args.model, dtype=torch.float32).eval().cuda()
    load_s = time.perf_counter() - t0
    resolved = {"weights_file": wfile.name, "weights_sha256": sha256_file(wfile),
                "architectures": model.config.architectures, "encoder": model.config.encoder.model_type,
                "decoder": model.config.decoder.model_type, "dtype": str(next(model.parameters()).dtype),
                "transformers": transformers.__version__, "torch": torch.__version__}
    require_equal("weights sha256", args.weights_sha256, resolved["weights_sha256"])
    require_equal("dtype", "torch.float32", resolved["dtype"])
    print("RESOLVED", json.dumps(resolved), flush=True)

    for page in args.pages:
        b = json.loads((Path(args.b_dir) / f"{Path(page).stem}.json").read_text())
        img = Image.open(page).convert("RGB")
        page_dark = darkness(np.asarray(img.convert("L")))
        p99 = float(np.percentile(page_dark, 99))
        torch.cuda.reset_peak_memory_stats()
        t = time.perf_counter()
        crops, gated = [], []
        for ln in b["lines"]:
            crop = warp_crop(img, ln["poly"])
            ink = int((darkness(np.asarray(crop.convert("L"))) >= GATE_KEEP_RATIO * p99).sum())
            crops.append(crop)
            gated.append((not args.no_gate) and ink < GATE_MIN_INK_PX)
            ln["_ink_px"] = ink
        crop_s = time.perf_counter() - t
        t = time.perf_counter()
        texts: dict[int, tuple[str, str, float, float, bool]] = {}
        todo = [i for i, g in enumerate(gated) if not g]
        for k in range(0, len(todo), args.batch):
            idx = todo[k:k + args.batch]
            pv = processor(images=[crops[i] for i in idx], return_tensors="pt").pixel_values.cuda()
            with torch.no_grad():
                gen = model.generate(pv, num_beams=1, do_sample=False, max_new_tokens=args.max_new_tokens,
                                     output_scores=True, return_dict_in_generate=True)
            trans = model.compute_transition_scores(gen.sequences, gen.scores, normalize_logits=True)
            dec = processor.batch_decode(gen.sequences, skip_special_tokens=True)
            for j, i in enumerate(idx):
                ids = gen.sequences[j, 1:]
                valid = ids != processor.tokenizer.pad_token_id
                probs = torch.exp(trans[j][valid]).tolist()
                n_gen = int(valid.sum())
                texts[i] = (undo_iam_spacing(dec[j]), dec[j], sum(probs) / len(probs) if probs else 0.0,
                            min(probs) if probs else 0.0, n_gen >= args.max_new_tokens)
        torch.cuda.synchronize()
        rec_s = time.perf_counter() - t
        out_lines = []
        for i, ln in enumerate(b["lines"]):
            base = {"box": ln["box"], "poly": ln["poly"], "b_text": ln["text"], "b_score": ln["score"], "ink_px": ln["_ink_px"]}
            if gated[i]:
                out_lines.append({"text": "", "text_raw": "", "score": 0.0, "min_token_prob": 0.0, "flags": [NO_TEXT], **base})
            else:
                txt, raw, sc, mn, trunc = texts[i]
                out_lines.append({"text": txt, "text_raw": raw, "score": round(sc, 4), "min_token_prob": round(mn, 4),
                                  "flags": ["TRUNCATED"] if trunc else [], **base})
        record = {
            "engine": "trocr-line", "model_revision": args.revision, "resolved": resolved,
            "detector": b.get("resolved", {}).get("det", {}).get("name", "engine_b (see b_dir)"),
            "postprocess": "undo_iam_spacing (format only)", "crop": "perspective warp of detector quad + pad",
            "gate": None if args.no_gate else {"keep_ratio": GATE_KEEP_RATIO, "min_ink_px": GATE_MIN_INK_PX},
            "decoding": {"num_beams": 1, "do_sample": False, "max_new_tokens": args.max_new_tokens, "dtype": "fp32"},
            "crop_pad_px": PAD, "page": Path(page).name, "latency_s": round(crop_s + rec_s, 3),
            "crop_s": round(crop_s, 3), "recognition_s": round(rec_s, 3), "model_load_s": round(load_s, 2),
            "n_lines": len(out_lines), "n_no_text": sum(gated), "n_truncated": sum(1 for l in out_lines if "TRUNCATED" in l["flags"]),
            "lines": out_lines,
            "peak_torch_alloc_mib": round(torch.cuda.max_memory_allocated() / 2**20),
            "peak_rss_mib_process": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        }
        (out_dir / f"{Path(page).stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        print(f"{Path(page).name}: crop {crop_s:.2f}s rec {rec_s:.2f}s lines={len(out_lines)} no_text={sum(gated)} "
              f"trunc={record['n_truncated']} peak_vram={record['peak_torch_alloc_mib']}MiB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
