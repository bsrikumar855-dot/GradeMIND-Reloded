"""Phase 0 spike: Engine A (Unlimited-OCR) via a running vLLM OpenAI-compatible server.

Same per-page JSON shape as run_engine_a.py (raw, regions, token logprobs, degenerate flags), so
summarize.py and score.py work unchanged. Request recipe per https://recipes.vllm.ai/baidu/Unlimited-OCR:
prompt starts with '<image>', skip_special_tokens=False, per-request ngram_size=35 / window_size=128.
Run with the engine_a venv (reuses the region parser and degenerate detector).
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from degenerate import degenerate_checks  # noqa: E402
from run_engine_a import PROMPT, parse_regions  # noqa: E402


def gpu_used_mib() -> int | None:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    ap.add_argument("--served-model", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--serving-note", required=True, help="e.g. 'vllm image digest + --quantization fp8'")
    ap.add_argument("--pages", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for page in args.pages:
        b64 = base64.b64encode(Path(page).read_bytes()).decode()
        body = {
            "model": args.served_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            "max_tokens": args.max_tokens, "temperature": 0.0, "logprobs": True, "top_logprobs": 2,
            "skip_special_tokens": False, "vllm_xargs": {"ngram_size": 35, "window_size": 128},
        }
        req = urllib.request.Request(args.url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        t = time.perf_counter()
        with urllib.request.urlopen(req, timeout=3600) as resp:
            data = json.loads(resp.read())
        latency = time.perf_counter() - t
        choice = data["choices"][0]
        raw = choice["message"]["content"] or ""
        lp_items = (choice.get("logprobs") or {}).get("content") or []
        logprobs = [it["logprob"] for it in lp_items]
        margins = [it["top_logprobs"][0]["logprob"] - it["top_logprobs"][1]["logprob"]
                   for it in lp_items if len(it.get("top_logprobs") or []) >= 2]
        probs = [math.exp(x) for x in logprobs]
        n_tokens = data.get("usage", {}).get("completion_tokens", len(logprobs))
        regions = parse_regions(raw)
        record = {
            "engine": "unlimited-ocr", "model_revision": args.revision, "path": "vllm",
            "serving": args.serving_note, "mode": "gundam (server default for single image)", "prompt": PROMPT,
            "decoding": {"temperature": 0.0, "max_length": args.max_tokens, "ngram_size": 35, "window_size": 128},
            "page": Path(page).name, "latency_s": round(latency, 3), "generated_tokens": n_tokens,
            "finish_reason": choice.get("finish_reason"),
            "tokens_per_s": round(n_tokens / latency, 1) if latency else None,
            "peak_torch_alloc_mib": None, "nvidia_smi_used_mib_after": gpu_used_mib(),
            "token_prob": {
                "mean": round(statistics.fmean(probs), 4) if probs else None,
                "min": round(min(probs), 4) if probs else None,
                "share_below_0.5": round(sum(p < 0.5 for p in probs) / len(probs), 4) if probs else None,
            },
            "token_logprobs": [round(x, 4) for x in logprobs], "token_margins": [round(x, 3) for x in margins],
            "degenerate_flags": degenerate_checks(raw, n_tokens, regions, args.max_tokens, choice.get("finish_reason")),
            "regions": regions, "raw": raw,
        }
        (out_dir / f"{Path(page).stem}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
        print(f"{Path(page).name}: {latency:.1f}s tokens={n_tokens} regions={len(regions)} "
              f"finish={choice.get('finish_reason')} flags={record['degenerate_flags']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
