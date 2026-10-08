# Owner Decisions Log

Append-only. Each entry is quoted or summarised from the owner, with its date. Superseded entries stay and are marked.

## 2026-10-08: Phase 0 review

| # | Decision | Consequence |
|---|---|---|
| D1 | **Unlimited-OCR as primary reader: rejected.** The final OCR strategy is deferred to Phase 0b on the same 27 pages: (a) PaddleOCR with the PP-OCRv5 *server* recogniser, (b) a line-level handwriting reader on PaddleOCR line crops, (c) show-through suppression, run with and without, (d) a cloud VLM ceiling reference **only after explicit written owner approval, with nothing sent to any cloud API before that**, (e) question-label detection accuracy per engine. | Phase 0b deliverable: `PHASE_0B_REPORT.md`, scored against OWNER_VERIFIED transcriptions only, ending with an engine-role recommendation. Then STOP. |
| D2 | **Unlimited-OCR serving:** no vendor-code patch now. Demoted to an optional cross-check / layout-hint engine, int8 transformers path only, **disabled by default** via the single provider config. vLLM + crop-cap patch is revisited only if Phase 0b shows cross-check value. The vLLM client is kept and marked "Implemented, untested at scale". | ARCHITECTURE DECISION 2 resolved; STATUS updated. |
| D3 | **GPU budget ≤ 6 GB VRAM, with fluctuation.** The OCR service processes one page at a time, pads pages to a fixed aspect ratio, survives a crash (health check → restart → resume from the last completed page, with stage-level caching), and treats GPU OOM as a retryable stage failure, never a silent skip. It needs an integration test that kills the OCR server mid-batch and asserts resume with no duplicate or missing pages. | Phase 1/2 requirement (there is no OCR service yet). Recorded in IMPLEMENTATION_PLAN. Phase 0b engines must also fit in ≤ 6 GB. |
| D4 | **First exam target: university internal assessments.** CBSE conventions are kept as a policy profile; neither is hard-coded. | Policy profiles in the rubric/ScoreComputer design (Phase 3). |
| D5 | **Data:** the owner verifies the 7 transcriptions and will try to provide a Maths/Physics sheet. Until then, numeral/sign/unit accuracy is **"UNTESTED" in every report**. | Reports must carry the UNTESTED label. |
| R | **Process rules** 9–11 added to spec §2: no editing a script during a run (copy it to the run dir), the detector is a backstop and needs fixtures, and agent drafts are never ground truth. | `spike/run_spike.sh` now snapshots the code into the run dir. |

## 2026-10-08: Consolidated Phase 0b review

| # | Decision | Consequence |
|---|---|---|
| D6 | **Data in repo approved.** Sample sheets, transcriptions, run outputs and real-page fixtures may be committed, provided cover pages and any name, register/roll number, signature or ID are redacted (black box in images; `[REDACTED]` in OCR text and transcriptions). Unredacted originals stay local and gitignored. `data/README.md` documents the source, approval date and redaction method. Page images and outputs over 1 MB go through **Git LFS**. | Needs `git-lfs` on the host (owner installs it). |
| R12 | **Rule 12: resolved config, not requested config.** | Runners read back the loaded models and hashes; a mismatch fails the run. OCR service: startup assertion plus a `/health/ocr` field. |
| D7 | **Detector fixtures committed** (redacted) so CI runs them. Rules without a real-page fixture get SYNTHETIC fixtures, still reported as "no real-page fixture yet". | |
| D8 | **Conditional preprocessing.** Page characterisation by measurable features. Variants: raw, binarised, background-normalised gray, gray + ruled/dotted-line removal. Best variant per page vs features; a deterministic selection rule, validated on the other sheet only. `PAGE_DETECTION_ANOMALY` when the detected line count is far above the expected density. | |
| D9 | **Engine C:** perspective-warp crops from the polygon; an empty-crop gate (`NO_TEXT`, skip TrOCR); line-detection quality (merged/split/missed/show-through); `TRUNCATED` flag; end-to-end and recognition-only timing. | |
| D10 | **Engine A base-mode int8** on all pages (raw + best variant), sequentially after the others. Per-page peak VRAM; `OVER_BUDGET` if > 6 GB, but the engine is kept in the test. | Needed to decide D2. |
| D11 | **Gate metrics are the core deliverable:** line-level, vs verified GT, per engine pair and the full set, at τ ∈ {0, 0.05, 0.10}: P(line wrong \| engines agree), gate recall, gate cost, oracle CER vs best single engine, shared autocorrections. | The recommendation is based on these plus the D8 selection rule. |
| D12 | **Proxy caveat:** the out-of-label digit count is labelled `PROXY_NON_NUMERICAL_SHEETS_ONLY` and becomes invalid once a Maths/Physics sheet arrives. | |
