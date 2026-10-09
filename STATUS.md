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

## Phase 1 (Foundation) progress

Each step is a separate commit; test numbers are from `make test` in the same session, and CI is the source of truth (rule 13).

| Step | State | Evidence |
|---|---|---|
| 1.1 uv workspace + single config source + provider registry | **Implemented and tested** | CI `37887515813` green |
| 1.2 DB schema + Alembic baseline, I8 append-only triggers, optimistic locking | **Implemented and tested** | CI `37887812722` green; `alembic check` clean |
| 1.3 Auth (argon2id + JWT), RBAC, error envelope, health endpoints | **Implemented and tested** | CI `37888752324` green |
| 1.4 Storage: single module (MinIO), UUID keys, upload validation, signed URLs, body-size limit | **Implemented and tested** | CI `37889396605` green (77 app tests, real Postgres + MinIO) |
| 1.5 Jobs + SSE: stage machine (idempotent claim, lease resume, content-hash cache, retry of the failed stage), Celery task, `/api/jobs/{id}`, `/events` (SSE, Last-Event-ID), `/retry`; INTAKE stage | **Implemented and tested** | CI `37889882133` green (94 app tests). The Celery task runs for real in the compose smoke (1.7) |
| 1.6 OCR service: PP-OCRv6 on CPU, rule-12 assertion at image build and at startup, `/ocr/page`, `/ocr/health`, `/ocr/version` | **Implemented and tested** | Unit tests (fake engine): 12 passed. Real container: output identical to Phase 0b `b_v6` on 3 pages (text, boxes, scores); a tampered weight hash makes it exit 3 before serving |
| 1.7 Docker Compose health-green | Not implemented | |
| 1.8 Web shell | Not implemented | |
| 1.9 CI: import-linter, single-path checks, tsc, compose smoke | Partial: ruff, mypy, pytest, alembic check are in CI | |

Known gaps in 1.4: an object written before a failed DB commit is orphaned (no sweeper yet); MIME sniffing checks magic bytes
only (deep PDF/image decoding happens at ingest in Phase 2); bucket creation is a call (`ensure_bucket`), to be run by the
compose init in 1.7.

Known gaps in 1.6: CPU latency is about 11–13 s per answer page, one page at a time (Phase 0b measured the same); the
API does not call `/ocr/page` yet (the OCR stage is Phase 2); `enable_mkldnn` is passed but cannot be read back (listed under
`not_verifiable` in `expected_models.json`).

Known gaps in 1.5: a job left QUEUED by a broker outage is not re-sent automatically yet (it is visible as QUEUED and can be
re-sent by a requeue sweep, planned with compose in 1.7); the lease is renewed only at stage boundaries, so a stage longer
than `job_lease_seconds` (default 900 s) could be resumed by a second worker (heartbeats come with the OCR stage in Phase 2).

## Implemented but untested

- `spike/engine_a/run_engine_a.py` modes `int8` and `offload` (exercised on real pages, with no automated tests).
- `spike/engine_b/run_engine_b.py` (exercised on 27 pages, with no automated tests).
- `spike/run_spike.sh`, `spike/summarize.py`, `spike/run_phase0b.sh` (code snapshot per rule 9), `spike/report_0b.py`.
- `spike/preprocess/show_through.py` v0.1.0 (visually checked on 3 pages; effect measured in Phase 0b).
- `spike/engine_c/run_line_htr.py` (TrOCR line reader; smoke-tested on 1 page).
- `spike/engine_a/run_engine_a_vllm.py` (vLLM client): **Implemented, untested at scale.** It ran 16 pages before the server OOM'd (OCR_SPIKE A7). Kept per D2.

## Not implemented

- Everything in [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md) §3–§20 beyond the Phase 1 steps above (pipeline, UI, ScoreComputer, most of invariants I1–I12).
