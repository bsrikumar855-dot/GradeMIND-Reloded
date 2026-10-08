# GradeMIND v2: Architecture (Phase 0 draft)

Status: **draft for owner review at the Phase 0 gate.** The binding spec is [MASTER_PROMPT.md](MASTER_PROMPT.md); this file records
how the spec maps onto the owner's actual host and what the OCR spike measured ([OCR_SPIKE.md](OCR_SPIKE.md)).
Items marked **DECISION** need owner approval before Phase 1.

## 1. Host constraints (measured, 2026-10-08)

| Constraint | Value | Consequence |
|---|---|---|
| GPU | RTX 5070, 12 GiB, sm_120 (Blackwell), CUDA 13 driver | All CUDA wheels/images must be cu128+ (torch 2.10 cu129 verified, Paddle 3.4 cu129 verified) |
| GPU sharing | About 4.65 GiB held by other processes; **owner decision: ~7 GiB budget** | At most one model on the GPU at a time. GPU stages are serialised by a single GPU worker |
| RAM | 31 GiB | Engine B on CPU peaks at 14.5 GB RSS. CPU OCR concurrency must be 1 |
| Docker | 29.8, GPU passthrough verified | OCR and LLM serving run as containers |

## 2. Services (spec §4, adjusted)

```text
web (Next.js) ──► api (FastAPI) ──► Postgres
                     │
                     ├──► Redis ◄── worker-cpu (Celery, concurrency=1 for OCR-B; N for light stages)
                     │                  └──► ocr-b (PaddleOCR, CPU container)
                     │
                     │          ◄── worker-gpu (Celery, concurrency=1, owns the GPU lease)
                     │                  ├──► ocr-a  (Unlimited-OCR; serving mode = DECISION, see §4)
                     │                  └──► model-gateway ─► llm (local Qwen, quantized; loaded only when OCR-A is unloaded)
                     │
                     └──► MinIO (S3-compatible object storage)
```

- **GPU lease.** One Redis-backed lease per host. A GPU stage acquires it, ensures its model is loaded (unloading the other), and
  runs. Pipeline throughput is therefore batch-oriented: OCR-A the whole batch, then swap, then the LLM for the whole batch. This is
  the only way both models fit in ~7 GiB.
- **OCR service API** stays as in spec §4 (`/ocr/page`, `/ocr/health`, `/ocr/version`), with one container per engine so the dependency
  stacks never mix (Paddle cu129 vs torch cu129 vs vLLM image).

## 3. OCR pipeline: what the spike changes

Spike facts (25 handwritten answer pages, 2 writers, 1 course; details and commands in OCR_SPIKE.md):

- **Engine A (Unlimited-OCR):** 4 of 25 pages produced degenerate output (counting loops, repeated tables, word loops, an invented Chinese date).
  Non-degenerate pages still showed silent word omission, misspelling normalisation (`segrade` → `separate`), real-word substitution
  (`Deforestation` → `Depreciation`) and invented digits. Token probability does not flag these: the mean was 0.997 on a counting loop. Region boxes are
  often page-sized `table` regions. The spec's `base`-mode retry cleanly recovered 0 of 4 pages.
- **Engine B (PaddleOCR PP-OCRv5, en mobile rec, CPU):** literal, line-level boxes, per-line scores. Its main failures are spelling-level
  misreads and **reading bleed-through (mirror writing from the reverse side) as content, including invented digits**.

Architectural consequences, independent of the strategy DECISION below:

1. **Preprocessing must suppress bleed-through** (spec §6 step 4). It must be validated on the owner sheets, measured as the count of
   bleed-through lines Engine B reports, not just CER.
2. **Highlight geometry comes from Engine B line boxes.** Engine A boxes are used only when they are line- or paragraph-sized.
3. **The degenerate detector is a growing list and is not sufficient by itself.** Any Engine A page that is flagged, or that disagrees with Engine B
   beyond tolerance, goes to `OCR_DEGENERATE` or `OCR_UNCERTAIN`, and the review shows both readings (I4).
4. **A numeral, option letter or unit present in only one engine's reading is `OCR_CRITICAL_TOKEN_MISMATCH`** (spec §5.3). Both
   engines invent digits on these sheets, so this rule will fire often. Expect a high review rate until reading quality improves.
5. **Engine A's `eval()` hazard.** The model's bundled post-processing `eval()`s generated text. Our parser never does, and a regression test
   (Phase 2) asserts that boxes are parsed with a JSON parser only.

### DECISION 1: OCR strategy (spec Phase 0 exit)

| Option | Description | Spike evidence for | Spike evidence against |
|---|---|---|---|
| A. Keep spec roles | Engine A primary text, Engine B cross-check | Engine A reads cursive prose words B misses (e.g. sheet_002 p12) | 16% degenerate pages, omissions and normalisation are exactly what grading cannot tolerate |
| **B. Swap roles (recommended to evaluate first)** | Engine B (literal, line boxes) is the primary text and geometry; Engine A is a per-line cross-check and layout hint | Literal reading, usable boxes, never "autocorrects" to dictionary words in the samples | Higher raw error on hard cursive; bleed-through must be fixed in preprocessing |
| C. Add a third reader | A line-level handwriting recogniser on Engine B's line crops, majority/agreement per line | Line crops avoid page-level degeneration; agreement of 2 of 3 is a stronger signal | Not measured yet; one more model to fit in the GPU lease |

No option can be approved on numbers yet, because **every CER/WER so far is against draft transcriptions** (NOT_REPORTABLE).

### DECISION 2: Engine A serving mode

Options: (i) int8 transformers (works within budget, 3–8× slower); (ii) vLLM FP8 with the image's `_UNLIMITED_OCR_MAX_CROPS`
patched from 32 to 24 plus fixed-aspect padding (a vendor-code patch, but it bounds memory); (iii) vLLM FP8 unpatched, which needs guaranteed GPU
headroom (e.g. interviewbot moved off this GPU). This choice only matters if DECISION 1 keeps Engine A.

| Mode | Peak GPU | Speed (normal page) | Fidelity vs bf16 | Status |
|---|---|---|---|---|
| bf16, transformers | OOM on shared GPU | n/a | reference | fails |
| bf16 + expert CPU offload | ~3.9 GiB | ~20× slower than int8 | exact | reference only |
| int8 (bitsandbytes), transformers | 6648 MiB gundam / 4361 MiB base | 3–23 s/page | same degeneration on p2 as bf16 | works |
| FP8, vLLM `unlimited-ocr` image | weights 3.57 GiB, needs ≈6.9 GiB free at start | **~2 s/page** | same degenerate pages as int8 (16/16) | **unstable on shared GPU**: OOM on 30-crop page, start fails when free memory dips (OCR_SPIKE A7) |

## 4. LLM grading (local Qwen, owner decision)

- `~/models/Qwen3-8B` is bf16 (~16 GB), which does not fit. Production needs a **4-bit build of about 5 GB** (AWQ or GPTQ for vLLM, or GGUF Q4_K_M
  for llama.cpp/Ollama). Which build is still to be chosen and pinned in Phase 3.
- It runs only while holding the GPU lease, after OCR-A is unloaded. It is served behind the model gateway (I7) with `temperature=0`, a pinned
  revision and prompt versions, and cached raw responses (I12).

## 5. Data protection

- The repo is public, so student scans, transcriptions and OCR outputs are gitignored (`data/README.md`). Production storage is MinIO only,
  with signed URLs (spec §16).
- Engine B downloads its models from Hugging Face on first use. In production, models are pre-fetched into the image, and the OCR containers run
  without network egress.
