# Phase 0: Discovery Notes

Status: **in progress, blocked on owner inputs** (see "Open questions").
Spec: [MASTER_PROMPT.md](MASTER_PROMPT.md), Sections 0, 21 and 23.

## 1. Section 0 inputs

| Input | Value | Source |
|---|---|---|
| GPU available | NVIDIA GeForce RTX 5070, 12227 MiB VRAM, compute capability 12.0 (Blackwell), driver 595.84 | detected: `nvidia-smi` |
| OS / runtime | Ubuntu 24.04.4 LTS, kernel 7.0.0-34, 24 CPU threads, 31 GiB RAM, ~354 GB free disk | detected |
| Sample answer sheets | **missing** | owner |
| Sample marking schemes | **missing** | owner |
| Primary subjects (P0) | **missing** | owner |
| Evaluation LLM (default) | **missing** (`~/models/Qwen3-8B` exists locally, which is a candidate the owner must confirm) | owner |
| Old GradeMIND repo | none found under `/home/Shreekumar` (depth 4) | needs owner confirmation |

Detection commands (run 2026-10-08):

```text
$ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
name, memory.total [MiB], driver_version
NVIDIA GeForce RTX 5070, 12227 MiB, 595.84
$ nvidia-smi --query-gpu=compute_cap --format=csv,noheader
12.0
```

## 2. Toolchain on the host

| Tool | Found | Spec needs | Gap |
|---|---|---|---|
| Python | 3.12.3 | 3.12 | ok |
| uv | 0.12.3 | yes | ok |
| git / gh | 2.43.0 / 2.67.0 (logged in as `bsrikumar855-dot`) | yes | ok |
| Docker | 29.8.0, nvidia-ctk installed | yes, with GPU | **user is not in the `docker` group**: `permission denied ... /var/run/docker.sock` |
| Node | 18.19.1 | current stable Next.js | Node 20+ is needed for current Next.js (Phase 1 gap, not Phase 0) |
| pnpm | missing | yes | Phase 1 gap |

## 3. OCR engine facts (checked against upstream, 2026-10-08)

**Engine A: `baidu/Unlimited-OCR`** (Hugging Face API + model card)
- Exists, license MIT, `pipeline_tag=image-text-to-text`, last modified 2026-07-29.
- Architecture `UnlimitedOCRForCausalLM` (files `deepencoder.py`, `modeling_deepseekv2.py`: a DeepSeek-OCR-style encoder and decoder).
- Weights: a single bf16 safetensors file of **6.67 GB**.
- Serving: an official vLLM image `vllm/vllm-openai:unlimited-ocr` (CUDA 13.0), plus a `-cu129` variant for Hopper. A custom SGLang dev wheel ships in the repo. The transformers path is tested with torch 2.10.0 and transformers 4.57.1.
- Single-image modes are `gundam` (base_size=1024, image_size=640, crop_mode=True) and `base`.

**Engine B: PaddleOCR.** The PyPI latest is `paddleocr` 3.7.0. The `paddlepaddle-gpu` PyPI latest is only 2.6.2, which predates Blackwell, so the GPU build will likely need Paddle's own wheel index. I'll verify that during the spike; the CPU fallback is acceptable for Engine B.

**vLLM:** the PyPI latest is 0.31.0.

## 4. Early risks (to be measured, not assumed)

1. **VRAM budget, 12 GB.** OCR weights alone take ~6.7 GB, leaving ~4–5 GB for KV cache and activations at `gpu_memory_utilization≈0.9`. The local LLM cannot be co-resident: Qwen3-8B in bf16 needs ~16 GB. Options are sequential GPU stages (unload OCR, then load the LLM), a 4-bit quantized LLM, or a cloud LLM (opt-in, with redaction). This is an owner decision.
2. **Blackwell (sm_120) support.** This needs CUDA 12.8 or later. The driver supports CUDA 13, so the default `unlimited-ocr` vLLM image should work. Paddle GPU support is unverified.
3. **Handwriting distribution shift** (spec §5.5). Published numbers are for printed documents. Nothing is known until the measurements run on real sheets.

## 5. Spike plan, once the inputs arrive

1. `sudo usermod -aG docker $USER` (owner action), then pull `vllm/vllm-openai:unlimited-ocr` and serve it with `temperature=0` and logprobs on.
2. PaddleOCR 3.x in an isolated uv venv under `spike/` (GPU if a Blackwell wheel exists, otherwise CPU).
3. Run both engines on 10–20 owner-supplied pages. Record raw outputs, latency, and peak VRAM (`nvidia-smi --query-gpu=memory.used` sampling).
4. Score CER/WER and numeral, sign, and unit accuracy against **owner-provided** transcriptions of 5 pages. Log autocorrection incidents.
5. Write `docs/OCR_SPIKE.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, and `PHASE_0_REPORT.md`, then STOP.

## 6. Open questions for the owner

See the chat or session log dated 2026-10-08. This section will be updated with the answers.
