# Phase 0: Discovery Notes

Status: **in progress, blocked on owner inputs** (see "Open questions").
Spec: [MASTER_PROMPT.md](MASTER_PROMPT.md), Sections 0, 21 and 23.

## 1. Section 0 inputs

| Input | Value | Source |
|---|---|---|
| GPU available | NVIDIA GeForce RTX 5070, 12227 MiB VRAM, compute capability 12.0 (Blackwell), driver 595.84 | detected: `nvidia-smi` |
| OS / runtime | Ubuntu 24.04.4 LTS, kernel 7.0.0-34, 24 CPU threads, 31 GiB RAM, ~354 GB free disk | detected |
| Sample answer sheets | 1 booklet, 12 pages (cover + 11 handwritten), at `data/samples/sheet_001/` (gitignored) | owner, 2026-10-08 |
| Sample marking schemes | **missing**; there is also **no question paper** for sheet_001 | owner |
| Primary subjects (P0) | Not stated. sheet_001 is a university CIA in Environmental Science and Sustainability (B.Tech, Anna University-affiliated), **not CBSE** | inferred from the sheet; owner to confirm |
| Evaluation LLM (default) | **Local Qwen** (`~/models/Qwen3-8B`) | owner, 2026-10-08 |
| Old GradeMIND repo | none; use the new repo `bsrikumar855-dot/GradeMIND-Reloded` | owner, 2026-10-08 |

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
| Docker | 29.8.2, nvidia-ctk installed | yes, with GPU | **resolved 2026-10-08**: the owner was added to the `docker` group. GPU passthrough verified: `docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu24.04 nvidia-smi` → `NVIDIA GeForce RTX 5070, 12227 MiB, 595.84`. Shells opened before the change need a re-login, or `sg docker` |
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

## 3b. Sample sheet_001 characteristics (observed, not measured)

- Source: Adobe Scan (Android) PDF. Pages are embedded JPEGs at 2400–2560 × 3150–3680 px (~290–335 ppi), extracted losslessly with `pdfimages -j`.
- The **cover page** has printed form fields plus handwritten register number, name, course code and date (PII), and an empty marks grid.
- Handwritten pages use ruled lines and a left margin. **Heavy phone shadow** falls across the right half of most pages.
- **Bleed-through:** mirrored writing from the reverse side is visible on most pages, and an engine may read it as content.
- A faint printed watermark (institution crest and slogan) sits mid-page.
- Answer labelling is irregular: `11.] a.]`, `13.]`, `19.] A.]`, parts headed "Part-A … Part D", and stray margin marks (`1'`). Part A answers include MCQ option letters (`c] plants`, `a) circular`): these are critical tokens.
- Numerals are rare: question numbers and two percentages (`90.%`, `9?.%`). This sheet barely exercises numeral, sign and unit accuracy, so a numerical-subject sheet is needed for that.

## 3c. Security finding: Unlimited-OCR remote code `eval()`s model output

`modeling_unlimitedocr.py` (rev `07dea832e22aefee32ad281d4b80551282e1c168`) calls Python `eval()` on
generated text: on box strings in `extract_coordinates_and_label` and on line data when `save_results=True`.
Generated text is derived from student handwriting, so this is a code-execution path controlled by the input.
**Mitigation in the spike:** always `eval_mode=True, save_results=False`, and boxes are parsed with `json.loads`
(`spike/engine_a/run_engine_a.py`). In production the vLLM path does not run this file's post-processing, and
our parser must never use `eval`. I'll add a regression test in Phase 2.

## 4. Early risks (to be measured, not assumed)

0. **The GPU is shared (observed 2026-10-08).** About 4.6 GiB of the 12 GiB is already in use before GradeMIND loads anything:
   PID 2024 `/home/techpark-9/important/interviewbot-sys2/venv/bin/python3` holds 3906 MiB (another account's service,
   not touched), and the desktop, Chrome and sunshine hold about 0.65 GiB. That leaves about 7.4 GiB of headroom against 6.7 GB of OCR weights.
   The production design cannot assume an exclusive GPU on this host.
   **Owner decision (2026-10-08): plan for a shared GPU, with about 7 GB as the real budget, including in production.** Consequences to validate in the spike:
   - Engine A must fit in about 7 GB. Measure the peak; if it is out of memory in bf16, evaluate a quantized or alternative serving setup.
   - The local Qwen grader cannot share the GPU with Engine A. GPU stages must run one at a time (OCR, then unload, then LLM), and Qwen must be quantized (4-bit is about 5 GB for 8B).
   - Engine B (PaddleOCR) should default to CPU so it never competes for VRAM; measure its CPU throughput.

1. **VRAM budget, 12 GB.** OCR weights alone take ~6.7 GB, leaving ~4–5 GB for KV cache and activations at `gpu_memory_utilization≈0.9`. The local LLM cannot be co-resident: Qwen3-8B in bf16 needs ~16 GB. Options are sequential GPU stages (unload OCR, then load the LLM), a 4-bit quantized LLM, or a cloud LLM (opt-in, with redaction). This is an owner decision.
2. **Blackwell (sm_120) support.** This needs CUDA 12.8 or later. The driver supports CUDA 13, so the default `unlimited-ocr` vLLM image should work. Paddle GPU support is unverified.
3. **Handwriting distribution shift** (spec §5.5). Published numbers are for printed documents. Nothing is known until the measurements run on real sheets.

## 5. Spike plan, once the inputs arrive

1. Engine A via the **transformers path** first (no Docker needed; spike only). Later, once Docker access works, use the `vllm/vllm-openai:unlimited-ocr` image to confirm vLLM parity and throughput.
2. PaddleOCR 3.x in an isolated uv venv under `spike/` (GPU if a Blackwell wheel exists, otherwise CPU).
3. Run both engines on 10–20 owner-supplied pages. Record raw outputs, latency, and peak VRAM (`nvidia-smi --query-gpu=memory.used` sampling).
4. Score CER/WER and numeral, sign, and unit accuracy against **owner-provided** transcriptions of 5 pages. Log autocorrection incidents.
5. Write `docs/OCR_SPIKE.md`, `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`, and `PHASE_0_REPORT.md`, then STOP.

## 6. Open questions for the owner

1. Verify the 5 draft transcriptions in `data/transcriptions/sheet_001/` (status `DRAFT_UNVERIFIED`; see `docs/TRANSCRIPTION_GUIDE.md`).
2. More sheets: different writers, plus at least one numerical subject (Maths or Physics). One writer is too narrow a sample to approve an OCR strategy.
3. The question paper and marking scheme for sheet_001. Not needed for the OCR spike; needed from Phase 2 on.
4. Target market: the spec says CBSE, but the sample is university CIA. Which comes first?
5. ~~Docker group membership~~: resolved.
