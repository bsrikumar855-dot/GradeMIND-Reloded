# PHASE 0 REPORT: Discovery and OCR spike

**Status: STOP. Waiting for owner input.** The exit criterion (spec §21: "owner approves the OCR strategy") is **not met yet**.
The spike runs are complete, but no CER/WER can be reported until the transcriptions are owner-verified.

Date: 2026-10-08. Repo: `bsrikumar855-dot/GradeMIND-Reloded` (public; student data gitignored).

## 1. What was built

| Item | Where |
|---|---|
| Binding spec in repo | `docs/MASTER_PROMPT.md` |
| Host discovery, owner inputs, sample sheet traits, security finding | `docs/PHASE0_DISCOVERY.md` |
| Engine A runner (transformers: bf16, int8, bf16+expert-offload) and vLLM client | `spike/engine_a/` |
| Engine B runner (PaddleOCR 3.7 / Paddle 3.4 cu129, CPU) | `spike/engine_b/` |
| Degenerate-output detector (11 checks, each added after a real miss) | `degenerate_checks` in `spike/engine_a/run_engine_a.py` |
| Scorer (CER/WER, critical tokens, autocorrect candidates; refuses unverified GT) | `spike/score.py` |
| Driver, per-page summarizer, aspect-padding probe | `spike/run_spike.sh`, `spike/summarize.py`, `spike/pad_aspect.py` |
| Results log with commands and raw outputs | `docs/OCR_SPIKE.md` |
| Architecture and plan drafts with open decisions | `docs/ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md` |
| Transcription guide and 7 draft transcriptions (local only) | `docs/TRANSCRIPTION_GUIDE.md`, `data/transcriptions/` |

## 2. Data

2 booklets from the same course (Environmental Science CIA-I, university, not CBSE), 2 writers, 27 pages (25 answer pages):
sheet_001 (Adobe Scan, ~330 ppi, heavy shadow, bleed-through) and sheet_002 (WhatsApp scan, ~200–280 ppi, near-binarised, hard cursive).
There are no question papers, no marking schemes, and **no numerical subject**.

## 3. Key measurements (commands and raw output are in `docs/OCR_SPIKE.md`)

Engine A (Unlimited-OCR, rev `07dea832`):
- bf16 does **not fit** the shared GPU: `OutOfMemoryError ... 141.25 MiB is free ... this process has 6.82 GiB` (A1).
- int8 fits (peak 6648 MiB). Normal pages take 3–23 s (A2, A4).
- **4 of 25 answer pages degenerate** (counting loops, repeated tables, word loops, empty-cell spam, an invented `2017年1月1日`) (A4).
- Exact bf16 degenerates too on the page tested, so it is model behaviour and not quantization: 29,436 tokens of counting, mean token prob 0.9967 (A3).
- The spec's `base`-mode retry cleanly recovers **0 of 4** (A6).
- Even on non-flagged pages it shows **omission, misspelling normalisation and real-word substitution** (A4).
- vLLM FP8 takes about **2 s/page** and agrees with int8 on which pages degenerate (16 of 16), but on the shared GPU it OOMs on 30-crop pages
  and fails to start when free memory dips. The crop count (12–30 per page) is driven by aspect ratio (A7).

Engine B (PaddleOCR PP-OCRv5, en mobile rec, CPU):
- Two CPU-path bugs were worked around (oneDNN/PIR crash, and a 46.5 GB alloc without a detection size cap) (B1).
- About **21–24 s/page with 14.5 GB peak RSS** (B2, B3).
- It reads **bleed-through as content, including invented digits** (`89103`, `108`, `810`, …) (B2, B3).

Both engines read **question labels** unreliably. That is the main alignment signal.

## 4. Decisions needed from the owner

1. **Verify the transcriptions** (`data/transcriptions/sheet_00{1,2}/`, 7 pages) against the images, then set `"status": "OWNER_VERIFIED"`.
   Without this, nothing is reportable.
2. **DECISION 1: OCR strategy** (`docs/ARCHITECTURE.md` §3). Recommendation: evaluate **option B (Engine B primary, Engine A as
   cross-check)** and **option C (add a line-level third reader on Engine B's line crops)** on the verified transcriptions before committing.
   Option A (the spec's current roles) is contradicted by the spike evidence.
3. **DECISION 2: Engine A serving mode** (only if Engine A stays): int8 transformers, a vLLM FP8 image patched to a 24-crop max, or
   freeing GPU headroom.
4. **More data:** at least one numerical-subject sheet (Maths/Physics), plus question papers and marking schemes for the golden set.
5. **Target exams:** CBSE (as the spec says) or university CIA (as the sample is).

## 5. Known gaps

- No reportable CER/WER (draft transcriptions only; draft-scored files are stamped NOT_REPORTABLE).
- Numeral, sign and unit accuracy are essentially **unmeasured**, because the sheets contain almost no numerals.
- Bleed-through suppression and other preprocessing have not been tried (spec §6, Phase 2).
- Option C (a third reader) has not been measured.
- Engine B's server recogniser (vs mobile) has not been evaluated.
- Timings partly overlapped with other spike jobs (noted in OCR_SPIKE). These are not clean throughput benchmarks.

## 6. Risks

| Risk | Severity | Note |
|---|---|---|
| OCR on these handwriting distributions is poor in both engines | High | Grading quality is bounded by reading quality. Expect a high review rate (correct per I4) |
| Shared GPU with a fluctuating free share | High | Already caused 3 vLLM start failures. Needs a GPU lease and bounded per-page memory |
| Engine A invents digits and dates | High | Both are critical-token hazards. Cross-engine agreement is mandatory, not optional |
| Narrow sample (2 writers, 1 course) | Medium | Results may not generalise |
| Vendor code `eval()` on model output | Medium | Avoided in the spike. A regression test is planned for Phase 2 |

## 7. Housekeeping

- GPU released: the vLLM container was removed (`docker rm -f uocr-spike`). The transformers runs exit after each batch.
- Local-only artifacts: `data/samples/`, `data/transcriptions/`, `spike/runs/`, `spike/pages/`, `~/models/Unlimited-OCR` (6.4 GB),
  the vLLM image (27.7 GB), and the uv cache (~22 GB+).
