# STATUS

**Phases:** Phase 0b is closed (decisions D18–D23 in [docs/DECISIONS.md](docs/DECISIONS.md)). **Phase 0c** (cloud ceiling, benchmark only) is prepared but
**blocked on owner verification (D21)**. **Phase 1** (Foundation, scoped by D19) is in progress. Reports: [PHASE_0_REPORT.md](PHASE_0_REPORT.md),
[PHASE_0B_REPORT.md](PHASE_0B_REPORT.md).

## BLOCKING items

- **D23 data requirement:** no AI-suggestion feature ships until the benchmark covers **≥ 10 students, ≥ 2 subjects, ≥ 1 numerical subject**.
  Current: **2 students, 1 subject (Environmental Science), 0 numerical**.
- **D21:** Phase 0c results require both transcription manifests to be `OWNER_VERIFIED`. Current: both `AGENT_VERIFIED`.
- **D20:** public-release consent for sheet_001 and sheet_002 is **not recorded** (`data/README.md`). The sheets are already public under D6.

## Implemented and tested

- `spike/score.py`: CER/WER, critical-token recall, and autocorrect candidates. It refuses unverified ground truth unless `--allow-draft`
  is given (then the output is stamped NOT_REPORTABLE). Self-test: identity gives CER 0, and an injected digit error and a spelling autocorrection are detected (commit `cb3d906`).
- Engine A degenerate-output detector (`spike/engine_a/degenerate.py`, stdlib). **Tested** by `spike/tests/test_degenerate_checks.py`:
  8 rules fire on real-page fixtures, with 0 false positives on 31 reviewed-clean pages (`uvx --from pytest==9.1.1 pytest spike/tests -q -rs`:
  `9 passed, 4 skipped`). The 4 skipped rules have **no real-page fixture yet** (TRUNCATED_AT_MAX_LENGTH, REPEATED_LINE, EMPTY_OUTPUT,
  ALL_REGIONS_EMPTY); they have SYNTHETIC fixtures. The real-page fixtures are committed in redacted form (D6/D7), so CI runs them.
- `spike/score.py` v2 (omissions, label P/R, option letters, spurious digits). Self-tested on sheet_001 drafts: identity gives perfect scores on all
  metrics, and injected faults are all detected (commit `1808074`).

- `spike/stats.py` (Wilson, page bootstrap, NOT_DISTINGUISHABLE): unit-tested. `make test`: 25 passed, 4 skipped, exit 0 (commit `1411815`).
- `spike/verify_ui/` transcription verifier: end-to-end self-test on a scratch copy (save lifecycle, whitelist, 400 on bad input). No automated test file.
- Rule-12 resolved-config assertions: smoke-tested; wrong weight hash -> exit 1 for Engines A and C.

- CI (GitHub Actions, `make test` on push): first run `37884412695` on `fd6c55d` passed: 25 passed, 4 skipped, exit 0. CI status is the source of truth (rule 13).

- **Phase 0c (prepared, nothing sent):** pre-registered plan + decision rule (`docs/PHASE_0C_PLAN.md`, commit `5911e5c`), prompt
  `prompts/ocr_ceiling/v1.md`, runner `spike/ceiling/run_ceiling.py` (gates verified: exit 2 with 6 unmet gates; dry run 100 requests, sent=0),
  report `spike/report_0c.py` (refuses non-OWNER_VERIFIED GT; selftest on synthetic outputs). Unit tests in `spike/tests/test_ceiling.py`.

## Implemented but untested

- `spike/engine_a/run_engine_a.py` modes `int8` and `offload` (exercised on real pages, with no automated tests).
- `spike/engine_b/run_engine_b.py` (exercised on 27 pages, with no automated tests).
- `spike/run_spike.sh`, `spike/summarize.py`, `spike/run_phase0b.sh` (code snapshot per rule 9), `spike/report_0b.py`.
- `spike/preprocess/show_through.py` v0.1.0 (visually checked on 3 pages; effect measured in Phase 0b).
- `spike/engine_c/run_line_htr.py` (TrOCR line reader; smoke-tested on 1 page).
- `spike/engine_a/run_engine_a_vllm.py` (vLLM client): **Implemented, untested at scale.** It ran 16 pages before the server OOM'd (OCR_SPIKE A7). Kept per D2.

## Not implemented

- Everything in [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md) §3–§20 (application, API, DB, UI, ScoreComputer, invariants I1–I12).
