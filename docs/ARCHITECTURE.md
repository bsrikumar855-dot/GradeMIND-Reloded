# GradeMIND v2: Architecture (v1, after the Phase 0b review)

The binding spec is [MASTER_PROMPT.md](MASTER_PROMPT.md). The decisions that shape v1 are in [DECISIONS.md](DECISIONS.md) (D18–D23 in particular).
Evidence: [PHASE_0B_REPORT.md](../PHASE_0B_REPORT.md), [OCR_SPIKE.md](OCR_SPIKE.md).

## 1. What v1 is (D19)

**Examiner-assisted grading.** Software organises, displays, searches and audits. The **examiner decides every verdict**.

- OCR text is **assistive only**: search, highlighting, and pre-filling the region the examiner is looking at. It is **never** the input to an AI verdict in v1.
- The examiner picks a verdict level per rubric criterion. `ScoreComputer` (deterministic, `Decimal`) turns levels into marks.
  Audit trail, analytics and reports work as specified (spec §11–§17).
- **AI verdict suggestions are disabled** (`AI_SUGGESTIONS_ENABLED=false`, single config source). The LLM verdict contract, evidence
  verification and invariant tests (I1–I12) are kept in the codebase and CI behind that flag, ready for Phase 0c's outcome (D22).
- Every examiner OCR correction is captured as **line-level labelled data** (§5). This is the future fine-tuning dataset.

Why: on the Phase 0b sheets, the best engine's line-level CER is about 0.4, and at most 3 of 115 lines are read correctly by every engine (PHASE_0B_REPORT F2).
Automated grading from OCR text is not defensible on this distribution (I4).

## 2. Host constraints

| Constraint | Value | Consequence |
|---|---|---|
| GPU | RTX 5070, 12 GiB, sm_120, shared; owner budget **≤ 6 GB** (D3) | v1 needs **no GPU**: PP-OCRv6 runs on CPU. The GPU lease exists only for future GPU providers |
| RAM | 31 GiB | PP-OCRv6 peaks at about 3.3 GB RSS (v5 server needs about 16 GB, which is why it is not used) |
| CPU OCR time | PP-OCRv6 detection about 7.5–8.2 s/page | A 12-page booklet takes about 2–3 min of background OCR per worker |
| Docker | 29.8, GPU passthrough verified | Everything runs in Compose |

## 3. Services

```text
web (Next.js, TS) ──► api (FastAPI) ──► Postgres 16
                          │
                          ├──► Redis ◄── worker (Celery): ingest, characterisation, OCR orchestration, scoring, reports
                          │                 │            (all processing is jobs; HTTP never runs a pipeline)
                          │                 └──► ocr (PaddleOCR PP-OCRv6 medium, CPU container, internal HTTP)
                          │
                          └──► MinIO (S3-compatible): page images, crops, exports; signed URLs only
```

- **OCR provider registry (rule 7, I7)** is one config source. Providers and v1 state:

  | Provider | v1 state | Basis |
  |---|---|---|
  | `paddle_v6` | **enabled** (primary) | D18 |
  | `trocr_line` | **disabled** (flag) | D18 |
  | `unlimited_ocr` | **disabled / excluded** | D18, D2 |
  | `fake` | test only | I11 |

  A test asserts that a disabled provider is never called (spy transport).
- **Model gateway** (LLM): present, with every provider **disabled** in v1 (D19). Kill switch in the same config source.
- **Rule 12:** the OCR service checks at startup that the loaded det/rec model names and weight hashes equal the configured ones, refuses to start otherwise,
  and exposes them in `/health/ocr`.
- **D3 resilience:** one page per OCR call; crash → health check → restart → resume from the last completed page (stage-level cache keyed by
  content hash + component version); OOM or crash is a retryable stage failure, never a silent skip. Integration test
  `test_ocr_server_killed_mid_batch_resumes_without_dup_or_gap`.

## 4. Pipeline (v1)

`QUEUED → INGEST (PDF/images → pages, 300 DPI) → CHARACTERISE (features + PAGE_* flags + PAGE_DETECTION_ANOMALY) → OCR (PP-OCRv6, raw page;
transforms off, D18) → LABEL_CONFIRM (examiner) → ALIGN (confirmed labels only) → REVIEW (examiner verdicts) → SCORE → COMPLETED`

- **Preprocessing (D18):** characterisation is on; image transforms are off, until a validated selection rule exists (≥ 5 sheets).
- **Alignment (D18, D1e):** OCR'd question labels are unreliable (label recall ≤ 0.65 in Phase 0b). Per page, the examiner confirms or corrects each
  detected label, or marks a gap, with one keypress. Only confirmed labels drive the `question → regions` mapping.

## 5. Correction capture: the line-level labelled dataset (D19)

Every time an examiner corrects OCR text, one immutable row is appended (I8):

| Field | Purpose |
|---|---|
| `id`, `created_at`, `examiner_id` | provenance |
| `submission_id`, `page_id`, `line_id` | where |
| `crop_object_key`, `crop_sha256`, `crop_bbox`, `crop_polygon` | the exact image the text belongs to (stored in MinIO, never a public URL) |
| `page_image_sha256`, `preprocessing_version` | reproduce the crop |
| `ocr_provider`, `ocr_model_names`, `ocr_weights_sha256` (rule 12) | which reading was corrected |
| `ocr_text`, `corrected_text`, `edit_ops` | the label (literal, misspellings preserved, `[?]` allowed) |
| `consent_scope` (`local_only` / `public_release`), `exam_id`, `subject` | D20: whether this row may ever leave the machine |
| `supersedes_id` | corrections of corrections stay append-only |

Export to a training format is a later job and respects `consent_scope`.

## 6. Data protection (D6, D20)

The repo is public. Only redacted data is committed, after a visual contact-sheet check. `data/README.md` records per-sheet public-release consent, and a sheet
without it stays local. In the product, page images live in MinIO and are served by short-lived signed URLs; access to answer images is audit-logged.

## 7. Deferred (behind flags or later phases)

AI verdict suggestions (D19/D22), the TrOCR cross-check (D18), Unlimited-OCR (D18), preprocessing transforms (D18), cloud LLMs (spec §16,
per-organisation opt-in), and the numerical-subject evaluators (D23 data requirement first).
