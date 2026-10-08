# GradeMIND v2: Implementation Plan (Phase 0 draft)

The phases and gates are those of spec §21. This file records the order of work inside each phase and what the Phase 0 spike changed.
Every phase ends with `PHASE_<n>_REPORT.md` and a STOP for owner approval.

## Phase 0b: OCR strategy evaluation (current, per D1)

Server recogniser, line-level HTR reader, show-through suppression (with/without), label-detection accuracy. Deliverable: `PHASE_0B_REPORT.md`, scored against OWNER_VERIFIED transcriptions only. Then STOP.

## Phase 0: Discovery and OCR spike (done; gate reviewed 2026-10-08)

Done: hardware discovery, both engines running on the owner host, 27-page runs on both engines, degenerate detector, scorer,
spike log, architecture draft. Remaining before the gate:

1. The owner verifies the draft transcriptions (`data/transcriptions/sheet_00{1,2}`, 7 pages) → re-score → the first **reportable** CER/WER.
2. vLLM FP8 serving of Engine A on the shared GPU: memory, throughput, and fidelity vs int8 on the same pages.
3. The owner's decisions on OCR strategy (ARCHITECTURE.md, DECISION 1) and serving mode (DECISION 2).
4. Ideally, a numerical-subject sheet, so that numeral, sign and unit accuracy are measured at all.

## Phase 1: Foundation

Order: monorepo layout → `uv` workspace (api, worker, shared) and pnpm web → Docker Compose (Postgres 16, Redis, MinIO, api,
worker-cpu, worker-gpu, ocr-a, ocr-b, web) → health endpoints → Alembic baseline → auth + RBAC → storage (UUID keys, signed URLs) →
job system with SSE → CI (ruff, mypy, tsc, import-linter, `scripts/check_single_paths.sh`, pytest) → `STATUS.md`.

Host prerequisites: Node 20+ and pnpm (host currently has Node 18.19 and no pnpm), or build the web image only in Docker.

Spike-driven additions:
- **GPU lease** (Redis) and a single-concurrency `worker-gpu`. This replaces "OCR service always up" with load-on-demand per batch.
- The OCR containers pre-bake their model weights (no runtime Hugging Face downloads) and pin model revisions.
- **D3 resilience:** one page at a time, fixed-aspect padding, health check → restart → resume from the last completed page with stage caching,
  GPU OOM as a retryable failure. **Integration test `test_ocr_server_killed_mid_batch_resumes_without_dup_or_gap`.**
- Unlimited-OCR provider exists but is **disabled by default** in the single provider config (D2). A test asserts it is never called while disabled (I7).

## Phase 2: Document intelligence

Order: preprocessing (with the **bleed-through suppression** ablation on the owner sheets) → OCR adapters behind `OCRProvider` →
degenerate detector (ported from `spike/engine_a/run_engine_a.py`, with its spike outputs as regression fixtures) → reconciliation per the
approved DECISION 1 → region model → question-paper parsing + edit UI → segmentation/alignment → fix-alignment UI → structure and
alignment benchmark on the golden set.

Spike-driven additions:
- Regression fixtures: every degenerate shape seen in the spike (counting loop, `0`-run, word loop, empty-cell spam, repeated
  cell text, CJK on an English sheet) must be flagged by a test.
- Security test: the Engine A output parser never calls `eval` (bundled model code does).
- Question-label reading is weak in both engines, so alignment cannot rely on OCR'd labels alone. Spatial and continuation signals
  carry more weight, and `ALIGNMENT_REVIEW_REQUIRED` will be common at first.

**Blocker for this phase's exit:** a golden set with question papers and marking schemes. None have been supplied yet.

## Phase 3: Evaluation engine

Rubric model and validation → marking-scheme drafting + approval → deterministic evaluators → LLM verdict contract → evidence
verification → `ScoreComputer` (Decimal, Hypothesis, 100% branch coverage) → confidence (weakest link) → routing → model gateway
with kill switch → `test_invariant_I1..I12`.

Spike-driven addition: choose and pin a 4-bit Qwen build (about 5 GB) that runs under the GPU lease.

## Phases 4–5, P1

As in spec §21.

## Risks carried forward

| Risk | Evidence | Mitigation |
|---|---|---|
| High review rate from OCR disagreement | Both engines invent digits; labels are misread | Expected and correct per I4. Track the review rate in the benchmark, and improve reading (third reader, preprocessing) rather than relaxing gates |
| Sample is narrow | 2 writers, 1 course, no numerical subject | Collect more golden data before Phase 2 exit |
| Shared GPU | 4.65 GiB held by an unrelated service | GPU lease, quantized models, batch scheduling |
| Engine A degeneration shapes keep changing | 6 distinct shapes in 29 page-runs | Cross-engine agreement as the primary safety net; the detector is only a backstop |
