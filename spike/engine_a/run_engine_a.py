"""Phase 0 spike: run Unlimited-OCR (Engine A) page-by-page via the transformers path.

Spike-only code (spec §5.1 says transformers is the dev path; production serves via vLLM).
Writes one JSON per page with the verbatim raw output, parsed regions, per-token
log-probabilities of the greedy choice, latency, and peak VRAM.

Safety: the model's own `infer(save_results=True)` calls `eval()` on generated text.
We always use eval_mode=True / save_results=False and parse boxes with json.loads.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer, BitsAndBytesConfig, LogitsProcessor

from degenerate import degenerate_checks  # noqa: F401  (re-exported for summarize.py / vLLM client)

MODES = {
    "gundam": dict(base_size=1024, image_size=640, crop_mode=True),
    "base": dict(base_size=1024, image_size=1024, crop_mode=False),
}
PROMPT = "<image>document parsing."
MAX_LENGTH = 32768
# Model-card defaults. NOTE: the n-gram ban is a decoding constraint that can, in principle,
# alter a literal reading of genuinely repeated text; it is recorded in the output for provenance.
NO_REPEAT_NGRAM = 35
NGRAM_WINDOW = 128

REF_RE = re.compile(r"<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>", re.DOTALL)
DET_RE = re.compile(r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^\]]+\])\s*<\|/det\|>", re.DOTALL)


class TokenLogprobRecorder(LogitsProcessor):
    """Records log p(argmax) and the top1-top2 margin at each greedy step. Returns scores unchanged."""

    def __init__(self) -> None:
        self.logprobs: list[float] = []
        self.margins: list[float] = []
        self.token_ids: list[int] = []

    def __call__(self, input_ids: torch.LongTensor, scores: torch.FloatTensor) -> torch.FloatTensor:
        lp = torch.log_softmax(scores[0].float(), dim=-1)
        top = torch.topk(lp, 2)
        self.logprobs.append(float(top.values[0]))
        self.margins.append(float(top.values[0] - top.values[1]))
        self.token_ids.append(int(top.indices[0]))
        return scores


def parse_regions(raw: str) -> list[dict]:
    """Split raw output into regions at det markers. Text following a marker belongs to it."""
    markers = []
    for m in REF_RE.finditer(raw):
        markers.append((m.start(), m.end(), m.group(1).strip(), m.group(2)))
    for m in DET_RE.finditer(raw):
        markers.append((m.start(), m.end(), m.group(1).strip(), m.group(2)))
    markers.sort()
    regions = []
    for i, (start, end, label, box_txt) in enumerate(markers):
        nxt = markers[i + 1][0] if i + 1 < len(markers) else len(raw)
        try:
            box = json.loads(box_txt)
            if box and isinstance(box[0], (int, float)):
                box = [box]
        except (json.JSONDecodeError, TypeError):
            box = None
        regions.append({"category": label, "bbox_raw": box, "text": raw[end:nxt].strip(), "raw_marker": raw[start:end]})
    return regions



def nvidia_smi_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=MODES, default="gundam")
    ap.add_argument("--quant", choices=["none", "int8", "offload"], default="none",
                    help="int8: bitsandbytes LLM.int8 on decoder linears (vision/projector/lm_head bf16). "
                         "offload: exact bf16, routed experts of --offload-layers kept in CPU RAM and streamed")
    ap.add_argument("--max-length", type=int, default=MAX_LENGTH,
                    help="generation cap; spike runs use 4096 so runaway pages fail fast (flagged TRUNCATED)")
    ap.add_argument("--offload-layers", default="5-11", help="decoder layer range whose routed experts live on CPU")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    scratch = out_dir / "_infer_scratch"

    t0 = time.perf_counter()
    tok = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    load_kw: dict = dict(trust_remote_code=True, use_safetensors=True, torch_dtype=torch.bfloat16)
    if args.quant == "int8":
        load_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_8bit=True, llm_int8_skip_modules=["sam_model", "vision_model", "projector", "lm_head"])
        load_kw["device_map"] = {"": 0}
        model = AutoModel.from_pretrained(args.model, **load_kw).eval()
    elif args.quant == "offload":
        lo, hi = (int(x) for x in args.offload_layers.split("-"))
        weight_map = json.loads((Path(args.model) / "model.safetensors.index.json").read_text())["weight_map"]
        device_map: dict = {}
        # Module-prefix keys (not per-parameter) so unsaved buffers such as position_ids are covered.
        for name in weight_map:
            m = re.match(r"(model\.layers\.(\d+)\.mlp\.experts)\.", name)
            if m and lo <= int(m.group(2)) <= hi:
                device_map[m.group(1)] = "cpu"
                continue
            parts = name.split(".")
            if parts[0] != "model":
                key = parts[0]
            elif parts[1] != "layers":
                key = ".".join(parts[:2])
            else:
                key = ".".join(parts[:5] if parts[3] == "mlp" else parts[:4])
            device_map[key] = 0
        load_kw["device_map"] = device_map
        model = AutoModel.from_pretrained(args.model, **load_kw).eval()
    else:
        model = AutoModel.from_pretrained(args.model, **load_kw).eval().cuda()
    load_s = time.perf_counter() - t0

    recorder_box: dict = {}
    orig_generate = model.generate

    def generate_with_recorder(**kw):
        rec = TokenLogprobRecorder()
        kw["logits_processor"] = list(kw.get("logits_processor") or []) + [rec]
        recorder_box["rec"] = rec
        return orig_generate(**kw)

    model.generate = generate_with_recorder

    for page in args.pages:
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t = time.perf_counter()
        raw = model.infer(tok, prompt=PROMPT, image_file=page, output_path=str(scratch),
                          max_length=args.max_length, no_repeat_ngram_size=NO_REPEAT_NGRAM,
                          ngram_window=NGRAM_WINDOW, save_results=False, eval_mode=True,
                          temperature=0.0, **MODES[args.mode])
        torch.cuda.synchronize()
        latency = time.perf_counter() - t
        rec: TokenLogprobRecorder = recorder_box["rec"]
        probs = [math.exp(x) for x in rec.logprobs]
        regions = parse_regions(raw)
        record = {
            "engine": "unlimited-ocr", "model_revision": args.revision, "path": "transformers",
            "mode": args.mode, "mode_params": MODES[args.mode], "prompt": PROMPT, "quant": args.quant,
            "offload_layers": args.offload_layers if args.quant == "offload" else None,
            "decoding": {"temperature": 0.0, "max_length": args.max_length,
                         "no_repeat_ngram_size": NO_REPEAT_NGRAM, "ngram_window": NGRAM_WINDOW},
            "page": Path(page).name, "latency_s": round(latency, 3), "model_load_s": round(load_s, 2),
            "generated_tokens": len(rec.logprobs),
            "tokens_per_s": round(len(rec.logprobs) / latency, 1) if latency else None,
            "peak_torch_alloc_mib": round(torch.cuda.max_memory_allocated() / 2**20),
            "peak_torch_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20),
            "nvidia_smi_used_mib_after": nvidia_smi_used_mib(),
            "token_prob": {
                "mean": round(statistics.fmean(probs), 4) if probs else None,
                "min": round(min(probs), 4) if probs else None,
                "share_below_0.5": round(sum(p < 0.5 for p in probs) / len(probs), 4) if probs else None,
            },
            "token_ids": rec.token_ids,
            "token_logprobs": [round(x, 4) for x in rec.logprobs],
            "token_margins": [round(x, 3) for x in rec.margins],
            "degenerate_flags": degenerate_checks(raw, len(rec.logprobs), regions, args.max_length),
            "regions": regions,
            "raw": raw,
        }
        (out_dir / f"{Path(page).stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        print(f"{Path(page).name}: {latency:.1f}s tokens={len(rec.logprobs)} regions={len(regions)} "
              f"peak_alloc={record['peak_torch_alloc_mib']}MiB flags={record['degenerate_flags']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
