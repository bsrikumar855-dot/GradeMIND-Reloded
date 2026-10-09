# Dependencies

Spec §2 rule 6: every dependency is pinned, with a one-line justification. Exact resolved versions for the application are in `uv.lock`.

## Application (Phase 1+, uv workspace: `packages/core`, `apps/api`, `apps/worker`)

| Package | Pin | Why |
|---|---|---|
| pydantic / pydantic-settings | 2.14.0 / 2.15.0 | Schemas; the **single config source** with startup invariant validation (rule 7, I7/I10/I11) |
| fastapi / uvicorn[standard] | 0.143.0 / 0.54.0 | API service (spec §4) |
| python-multipart | 0.0.32 | FastAPI file uploads |
| sse-starlette | 3.5.0 | Job progress over SSE (spec §15) |
| celery[redis] | 5.6.3 | Job stages in the worker (spec §4/§15); Redis broker |
| minio (Python SDK) | 7.2.20 | S3 client; imported **only** in `grademind_core/storage.py` (the single storage module). Presigns locally with a fixed region |
| MinIO server image | `cgr.dev/chainguard/minio@sha256:f74600a1…fa18` (MinIO RELEASE.2026-09-22T19-25-18Z) | Object storage (compose + CI). `minio/minio` is no longer on Docker Hub and `quay.io/minio/minio` needs a login, so we use Chainguard's free image, **pinned by digest** |
| pytest / pytest-asyncio / httpx | 9.1.1 / 1.4.0 / 0.28.1 | Tests (dev) |
| ruff / mypy / import-linter | 0.16.10 / 2.4.0 / 2.15 | Lint, strict typing, SDK import boundaries (rule 7) (dev) |

## OCR service (`services/ocr`, CPU container; not a uv workspace member)

| Package / image | Pin | Why |
|---|---|---|
| python base image | `python:3.12-slim-bookworm@sha256:34386ef0…7258` | Same base as the API/worker image |
| paddlepaddle (CPU) | 3.4.0, from Paddle's CPU index | PyPI stops at 3.3.1. 3.4.0 is what Phase 0b measured; output is **bit-identical** to Phase 0b `b_v6` on 3 pages (text, boxes, scores) |
| paddleocr | 3.7.0 | PP-OCRv6 medium det+rec (D18) |
| everything else | `services/ocr/requirements.lock.txt` | `pip freeze` of the verified image, passed as pip constraints |
| model weights | sha256 in `services/ocr/expected_models.json` | Downloaded at build from Hugging Face (paddlex default) and checked against the Phase 0b hashes at build **and** at startup (rule 12) |

## Spike environments (Phase 0/0b; `spike/*/requirements.txt`)
Each engine has its own venv, so dependency stacks never mix (mirroring the per-engine containers planned in ARCHITECTURE §2).

## Engine A: Unlimited-OCR (`spike/engine_a/requirements.txt`). Optional cross-check, disabled by default (D2)

| Package | Pin | Why |
|---|---|---|
| torch / torchvision | 2.10.0 / 0.25.0 (cu129) | Model card's tested versions; the cu129 build includes sm_120 (Blackwell) kernels (verified) |
| transformers | 4.57.1 | Model card's tested version (the `trust_remote_code` model needs it) |
| bitsandbytes | 0.50.2 | int8 weights: bf16 does not fit the shared GPU (OCR_SPIKE A1) |
| accelerate | 1.15.0 | device_map for int8 and the bf16 expert-offload reference |
| einops 0.8.2, addict 2.4.0, easydict 1.13, Pillow 12.1.1, psutil 7.2.2, matplotlib 3.10.8, pymupdf 1.27.2.2 | as listed | Imported by the model's remote code (model card list) |

Model weights: `baidu/Unlimited-OCR` rev `07dea832e22aefee32ad281d4b80551282e1c168` (MIT), `model-00001-of-000001.safetensors` sha256 `2bc48a7a110061ea58fff65d3169367eebe3aee371ca6968dc2219c1b2855fc6` (matches the Hub LFS record; asserted at load per rule 12).
vLLM image (D2: kept, not used by default): `vllm/vllm-openai@sha256:542961a42d9183813819a23ef3a8b50bfb4f5ef7b0fb4f8e4f56edd8445efb18`.

## Engine B: PaddleOCR (`spike/engine_b/requirements.txt`)

| Package | Pin | Why |
|---|---|---|
| paddlepaddle-gpu | 3.4.0 (cu129 index) | The PyPI build stops at 2.6.2, which predates Blackwell. 3.4.0 cu129 passes `paddle.utils.run_check()` on the RTX 5070 |
| paddleocr | 3.7.0 | Current release. PP-OCRv5 det/rec. Line boxes and per-line scores |
| opencv (via paddlex) | 4.10.0 | Already present. Used by `spike/preprocess/show_through.py` (no extra dependency) |

Models (downloaded by PaddleX on first use; to be pre-baked and pinned in Phase 1): `PP-OCRv5_server_det`, `en_PP-OCRv5_mobile_rec`,
`PP-OCRv5_server_rec`, `PP-OCRv6_medium_det` (HF rev `8e0f56fb`), `PP-OCRv6_medium_rec` (HF rev `e5a92bcb`). From rule 12 on, each run records the
resolved model names and per-file sha256 of every loaded model directory, and fails if they differ from what was requested.

## Engine C: line-level handwriting reader (`spike/engine_c/requirements.txt`)

| Package | Pin | Why |
|---|---|---|
| torch / torchvision | 2.10.0 / 0.25.0 (cu129) | Same verified Blackwell build as Engine A (shared uv cache) |
| transformers | 4.57.1 | `VisionEncoderDecoderModel` / `TrOCRProcessor` |
| Pillow | 12.1.1 | Line cropping (perspective warp via `Image.QUAD`) |
| numpy | 2.5.3 | Empty-crop gate ink statistics (was transitive via torch; pinned once used directly) |

**Model choice: `microsoft/trocr-large-handwritten`, rev `e68501f437cd2587ae5d68ee457964cac824ddee`** (owner-suggested candidate, D1b).
- **For:** the most widely used open line-level handwriting recogniser with a ready Hub checkpoint. It is fine-tuned on IAM, and the TrOCR paper reports an IAM CER of 2.89.
  It runs on the already-verified torch/transformers stack. Its weights (about 2.2 GB, fp32) fit the ≤ 6 GB budget (D3). Inference is line-by-line, which matches
  the "line crops limit the page-level language prior" hypothesis.
- **Against, measured rather than assumed:** the decoder is autoregressive and initialised from a text language model, so it can
  still normalise spelling within a line. PP-OCRv5's recogniser (CTC, no autoregressive LM) is the natural contrast.
- **Alternatives considered:** HTR-VT (ViT + CTC, SOTA-class IAM results, code on GitHub) and HTR-JAND (reported IAM CER 1.23%).
  Neither publishes a ready Hub checkpoint for drop-in evaluation. They are candidates for a follow-up if TrOCR autocorrects.
  Sources: [HTR-VT](https://arxiv.org/pdf/2409.08573), [HF papers search](https://huggingface.co/papers?q=handwritten+text+recognition).
- Weights `pytorch_model.bin` sha256 `954bf2b50a871bb8e6e90ba0343d64d21055712f3d95d468995ea074481cb837` (matches the Hub LFS record; asserted at load per rule 12).
- **Note:** the checkpoint ships only `pytorch_model.bin` (pickle). transformers 4.57.1 loads it with `torch.load(weights_only=True)`. This is
  acceptable for a pinned first-party (Microsoft) checkpoint; production should convert it to safetensors once and pin the hash.

## Phase 0c ceiling (`spike/ceiling/requirements.txt`; benchmark only, D22)

| Package | Pin | Why |
|---|---|---|
| anthropic | 1.12.1 | Official SDK for the Claude ceiling run (`claude-fable-5-1`); imported only inside the runner |
| google-genai | 2.29.0 | Official SDK for the Gemini ceiling run; imported only inside the runner |

## Host tools used by the spike

`pdfimages` (poppler-utils, system) for lossless page extraction; `rsync` for run code snapshots (process rule 9); `/usr/share/dict/american-english` as the word list for autocorrect candidates.
