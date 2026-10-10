# STATUS

**Phases:** Phase 0b is closed (decisions D18–D23 in [docs/DECISIONS.md](docs/DECISIONS.md)). **Phase 0c** (cloud ceiling, benchmark only) is prepared but
**blocked on owner verification (D21)**. **Phase 1** is **approved** ([PHASE_1_REPORT.md](PHASE_1_REPORT.md)). **D26 hardening is done**; **Phase 2 (grading core) is APPROVED (D28)**; **Phase 3 (OCR assist) is APPROVED (D29)**; **Phase 4 (review polish, analytics, reports, pilot readiness) is in progress**: [PHASE_2_REPORT.md](PHASE_2_REPORT.md).
[PHASE_0B_REPORT.md](PHASE_0B_REPORT.md).

## BLOCKING items

- **D23 data requirement:** no AI-suggestion feature ships until the benchmark covers **≥ 10 students, ≥ 2 subjects, ≥ 1 numerical subject**.
  Current: **2 students, 1 subject (Environmental Science), 0 numerical**.
- **D21 / D27:** Phase 0c stays blocked until both transcription manifests are `OWNER_VERIFIED` **and** the owner confirms in chat. Current: both `AGENT_VERIFIED`. Nothing is sent to any cloud API.
- **D20 / D27:** public-release consent for sheet_001 and sheet_002 is **not recorded** (`data/README.md`). The sheets are already public under D6. Blocking item.

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

## Phase 4 (review polish, analytics, reports, pilot readiness; D29) progress

Each step is one commit with CI green before the next. Test numbers come from `make test` / CI in the same session (rule 13).

| Step | State | Evidence |
|---|---|---|
| 4.0 OCR hardening (own queue + worker, bounded automatic retry with backoff, "unread" + manual retry, sweeper covers failed readings, killed-and-restarted OCR test) | **Implemented and tested** | `48f1922` was RED (CI `38046106208`: the Phase 3 E2E read the audit table before its rows loaded; a test race, fixed in `bf387bd`). `bf387bd`, CI `38046363766` green (test, secrets, web, compose-smoke): 331 app tests; web unit tests 19/19; smoke: the ingest job has only INTAKE and RASTERIZE and the real engine read both pages through the `ocr` job; **`scripts/ocr_chaos.py` in CI: OCR killed at 1 of 4 pages, retried by itself (retry_count 1), restarted, 4 of 4 read, no manual retry, exactly one OK run per page, grading stayed possible** |
| 4.1 Admin UI: users, examiner assignment | Not started | |
| 4.2 Finalize / reopen / snapshots / verify command | Not started | |
| 4.3 Analytics | Not started | |
| 4.4 Reports | Not started | |
| 4.5 Accessibility + input | Not started | |
| 4.6 Pilot readiness (backup/restore, runbook, security pass) | Not started | |
| 4.7 E2E in CI | Not started | |

4.0 notes: machine reading is no longer part of the ingest job. When ingest completes, the worker creates an `ocr` job on the `ocr` queue,
served by the new `ocr-worker` compose service (concurrency 1); the default worker serves only page rendering (concurrency back to 2). A reading that fails because
the service was unavailable goes back to QUEUED with a `next_attempt_at` (30 s, 2 min, 8 min, then 15 min, at most 4 retries); the sweeper (every 60 s) sends it when due, so
a restart of any process loses nothing. After the last retry the booklet shows "Unread" and the Retry machine reading button. One active reading per booklet is enforced by
a unique index. Known limit: a deep OCR queue still collects one duplicate (harmless) message per sweep grace period for jobs it re-sends.

## Phase 3 (document intelligence, assistive only; D28) progress

Each step is one commit with CI green before the next. Test numbers come from `make test` / CI in the same session (rule 13).

| Step | State | Evidence |
|---|---|---|
| 3.0 Phase 2 gaps: keyboard-only box drawing; Playwright for examiner override-with-reason, attempts, crossed-out, remove region, N/P/?; stuck-QUEUED sweeper test | **Implemented and tested** | `cb3a03c`, CI `38032702947` green (test, secrets, web, compose-smoke): 247 app tests; web unit tests 8/8 in the image build; Playwright 6/6 (5 new + the Phase 2 spec) in compose-smoke |
| 3.1 OCR stage in the ingest job (registry-only OCR calls, config hash + resolved config stored and logged, append-only tables, idempotent, retry, page failures never block grading) | **Implemented and tested** | `8cd9aff`, CI `38033496929` green (test, secrets, web, compose-smoke): 268 app tests; the real engine read both pages of the smoke booklet through the worker (`pages_read 2, failed 0`); isolation contract kept (8/8); also checked locally: real client vs real OCR container, health + rule-12 OK, result size = page size |
| 3.2 Region text API (pure geometry) | **Implemented and tested** | `6f13d4a`, CI `38033939216` green. `make test` 289 passed. `ocr_geometry.py` 100% branch coverage (in the `make test` gate) with 16 tests incl. 4 Hypothesis properties; `GET /submissions/{id}/machine-reading` (9 API tests incl. "grading identical with and without OCR" and "no machine text in the workspace") |
| 3.3 "Machine reading" panel (per-region lines, low-confidence flag, "can be wrong" notice, hide toggle, keyboard focus outlines the line on the page, escaped text) | **Implemented and tested** | `645b41c` + guard fix `0243c46`, CI `38035170650` green (run `38034902000` on `645b41c` was RED: the new hidden-character guard flagged its own source; fixed). Playwright 10/10 in CI (4 panel tests use an intercepted response); web unit tests 14/14; 291 app tests |
| 3.4 Examiner line correction -> `line_corrections` (append-only linear chain per line, crop + provenance + edit ops, audit in the same transaction, original one click away) | **Implemented and tested** | `51fe2d9`, CI `38036400984` green (test, secrets, web, compose-smoke); 310 app tests; stale-edit guard mutation-checked; Playwright 15/15 locally against the new API |
| 3.5 Owner-only dataset export (consent-aware) | **Implemented and tested** | `9fd30b7`, CI `38036928042` green. `cli export-corrections` (admin-gated, audited, no HTTP route), `docs/DATASET_EXPORT.md`; 8 export tests on real Postgres + MinIO incl. the three exit codes and a mutation check on the consent filter; refuses to write inside the public repo unless git-ignored |
| 3.6 E2E with the real OCR engine: machine lines, a correction, audit trail, grading unchanged | **Implemented and tested** | `df48cdf` was RED (CI `38037318514`: compose-smoke, the OCR stage did not finish; the job FAILED after 10.46 s, consistent with the 10 s health timeout while the single OCR service was busy; **hypothesis, not confirmed**). Fix `60a0f62` (health retries 3 x 30 s, 3 unit tests, CI dumps jobs/ocr_runs on failure), CI `38038114654` green (test, secrets, web, compose-smoke): 322 app tests; Playwright 16/16 incl. `phase3.spec.ts` (32 s); `PHASE 3 EVIDENCE OK` from the real database |

3.1 notes: grading readiness is `pages_ready` (the RASTERIZE stage succeeded), not job completion, so a slow or dead OCR
service never blocks grading (the Booklets page offers "Grade" as soon as pages exist). The import-linter contract "OCR text
never reaches a verdict (D28)" (OCR tables moved to `db/ocr_models.py`, OCR API in `routes/ocr.py`) is proven to fire by
`tests/test_ocr_isolation.py` (direct and indirect plants). **Known limitation:** the OCR stage holds a worker slot for the whole
booklet (~12 s/page, one page at a time in the service), so bulk uploads are throughput-bound; worker slots raised 2 -> 6 as a
stop-gap; a dedicated OCR queue is the real fix (decision needed, see the Phase 3 report).

3.0 notes: the examiner for the E2E comes from the new `cli create-user` (admins still have no user-management UI; examiner
assignment is API-only). The N/P/? shortcuts were found to be broken once the active question had no answer box (they lived
in the grading panel, which unmounts there); they now live at workspace level and a regression test pins it. The sweeper
logic moved into `grademind_core.jobs.resend_stuck` and is tested end to end (lost enqueue -> re-sent -> job completes).

3.6 notes: the real engine read the synthetic PRINTED page exactly (3 lines, 122 characters, 0 errors, scores 0.9974 / 0.9999 / 0.9976).
This says nothing about handwriting (Phase 0b: best line CER about 0.43). Phase 3 is complete and **stopped at the phase gate**;
see [PHASE_3_REPORT.md](PHASE_3_REPORT.md). Phase 4 has not been started.

## D26 hardening (done before Phase 2)

| Item | State | Evidence |
|---|---|---|
| 1. Job lease heartbeats; reclaim after N missed beats; idempotent stage outputs | **Implemented and tested** | `9b14465`, CI `37917523441`. Tests: long stage not taken over; SIGKILLed worker reclaimed; lost-lease worker writes nothing; racing duplicate runs record one output; mutation check |
| 2. Every image digest-pinned; Actions SHA-pinned; Paddle wheels + OCR weights vendored | **Implemented and tested** | `11c711c`, CI `37919077714`. Release `ocr-vendor-v1`; OCR pip + weights steps are `RUN --network=none`; `tests/test_supply_chain.py` |
| 3. Secret scanning in CI (gitleaks, full history) + untracked-secrets tests | **Implemented and tested** | `aa3bcde`; CI job `secrets`. It caught synthetic fixtures in `6ad5d9f` (runs `37919467111`, `37919589673` red); fixed in `a5f0463`, CI `37919914065` green |
| 4. Log redaction + malformed-login regression tests | **Implemented and tested** | `6ad5d9f`; record factory redacts every logger; 7 malformed-login cases; JSON lines stay valid |
| 5. D19 line-correction schema | **Confirmed** | migration 0001; contract test; `docs/schema/line_corrections.txt` (`341110a`) |
| 6. README docker-group note | **Done** | `696ab1e` |

## Phase 1 (Foundation) progress

Each step is a separate commit; test numbers are from `make test` in the same session, and CI is the source of truth (rule 13).

| Step | State | Evidence |
|---|---|---|
| 1.1 uv workspace + single config source + provider registry | **Implemented and tested** | CI `37887515813` green |
| 1.2 DB schema + Alembic baseline, I8 append-only triggers, optimistic locking | **Implemented and tested** | CI `37887812722` green; `alembic check` clean |
| 1.3 Auth (argon2id + JWT), RBAC, error envelope, health endpoints | **Implemented and tested** | CI `37888752324` green |
| 1.4 Storage: single module (MinIO), UUID keys, upload validation, signed URLs, body-size limit | **Implemented and tested** | CI `37889396605` green (77 app tests, real Postgres + MinIO) |
| 1.5 Jobs + SSE: stage machine (idempotent claim, lease resume, content-hash cache, retry of the failed stage), Celery task, `/api/jobs/{id}`, `/events` (SSE, Last-Event-ID), `/retry`; INTAKE stage | **Implemented and tested** | CI `37889882133` green (94 app tests). The Celery task runs for real in the compose smoke (1.7) |
| 1.6 OCR service: PP-OCRv6 on CPU, rule-12 assertion at image build and at startup, `/ocr/page`, `/ocr/health`, `/ocr/version` | **Implemented and tested** | CI `37891158844` on `2a27784` was **red** (lint referenced `scripts/`, which only landed in the next commit); fixed by `91765b6`, CI `37891292406` green. Unit tests (fake engine): 12 passed. Real container: output identical to Phase 0b `b_v6` on 3 pages (text, boxes, scores); a tampered weight hash makes it exit 3 before serving |
| 1.7 Docker Compose: postgres, redis, minio, migrate, bootstrap (bucket + first admin), api, worker (+ requeue sweep), ocr | **Implemented and tested** | CI `37891292406` green (109 app tests); compose-smoke CI job in `37913482993` green. `docker compose up`: all 6 long-running services healthy; `scripts/compose_smoke.py` passed end to end (login, upload, Celery job via SSE, signed URL, `/health/ocr` rule12 OK). Web joins in 1.8; CI smoke in 1.9 |
| 1.8 Web shell: Next.js 16 App Router, TS strict, Tailwind 4, shadcn-style components; sign-in, dashboard (health), exam list; light theme | **Implemented and tested** | CI `37913136752` green; `web` + `compose-smoke` jobs in `37913482993` green. Image build runs `tsc --noEmit` and `eslint --max-warnings 0`; compose smoke checks the web flow (redirect without session, wrong password, CSRF 403, httpOnly cookie, dashboard, exam list, no token in HTML). Checked in a browser at 518 px width. No automated a11y audit or Playwright yet |
| 1.9 CI: import-linter (6 contracts), `scripts/check_single_paths.sh` (spec rule 3), web job (tsc, eslint, build), compose-smoke job | **Implemented and tested** | CI `37913482993` green: jobs `test` (6 contracts kept, 0 violations), `web`, `compose-smoke` (all healthy, SMOKE OK). Both checks proven to fire (11 planted-violation scanner tests; a planted `import minio` + api->worker import broke 2 contracts) |

Known gaps in 1.4: an object written before a failed DB commit is orphaned (no sweeper yet); MIME sniffing checks magic bytes
only (deep PDF/image decoding happens at ingest in Phase 2); bucket creation is a call (`ensure_bucket`), to be run by the
compose init in 1.7.

Known gaps in 1.8: no Playwright tests yet (spec §20 wants them for the upload → review flow, which arrives in Phases 2–4);
WCAG AA is by construction (labels, landmarks, skip link, focus rings, contrast-checked tokens), not by an automated audit.
Found and fixed while testing: sign-in redirected the browser to the container bind address (`http://0.0.0.0:3000`);
redirects are now relative and the smoke test asserts it.

Known gaps in 1.6: CPU latency is about 11–13 s per answer page, one page at a time (Phase 0b measured the same); the
API does not call `/ocr/page` yet (the OCR stage is Phase 2); `enable_mkldnn` is passed but cannot be read back (listed under
`not_verifiable` in `expected_models.json`).

Known gaps in 1.5: the lease is renewed only at stage boundaries, so a stage longer
than `job_lease_seconds` (default 900 s) could be resumed by a second worker (heartbeats come with the OCR stage in Phase 2).

## Phase 2 (Grading core) progress

CI is the source of truth (rule 13). Final head `6d0f7d6`, CI run `38023217004`: `test`, `secrets`, `web`, `compose-smoke` all green.

| Step | State | Evidence |
|---|---|---|
| 2.1 Grading schemas + ScoreComputer (pure, Decimal, versioned) | **Implemented and tested** | `e2d8627`; 100% branch coverage gate + Hypothesis |
| 2.2 Deterministic question-paper parser (no LLM) | **Implemented and tested** | `d855b67` |
| 2.3 Versioned paper + rubric API (drafts, validation, immutable approval) | **Implemented and tested** | `9c9677b` |
| 2.4 Booklet -> page images (RASTERIZE) + pages API | **Implemented and tested** | `db1d1ce` |
| 2.5 Regions, evaluations, overrides, score sheets, totals + CSV, audit API | **Implemented and tested** | `e48ec6b` |
| 2.6 Web: paper/rubric editors, booklets, page viewer, region mapping, grading workspace, totals, audit | **Implemented; tested through the E2E happy path only** | `89ee9fe` (red), `a98fbb7` (lint fix), `61bb827` (pages) |
| 2.7 Playwright E2E in CI (`compose-smoke`) | **Implemented and tested** | `ac90662`..`6d0f7d6`; run `38023217004` green; audit sequence printed as annotation |

Known gaps are listed in PHASE_2_REPORT.md (no browser tests for override, attempts, rotate/fit/pan, N/P/? keys; no keyboard-only region drawing; no a11y audit).

## Implemented but untested

- `spike/engine_a/run_engine_a.py` modes `int8` and `offload` (exercised on real pages, with no automated tests).
- `spike/engine_b/run_engine_b.py` (exercised on 27 pages, with no automated tests).
- `spike/run_spike.sh`, `spike/summarize.py`, `spike/run_phase0b.sh` (code snapshot per rule 9), `spike/report_0b.py`.
- `spike/preprocess/show_through.py` v0.1.0 (visually checked on 3 pages; effect measured in Phase 0b).
- `spike/engine_c/run_line_htr.py` (TrOCR line reader; smoke-tested on 1 page).
- `spike/engine_a/run_engine_a_vllm.py` (vLLM client): **Implemented, untested at scale.** It ran 16 pages before the server OOM'd (OCR_SPIKE A7). Kept per D2.

## Not implemented

- Everything in [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md) §3–§20 beyond the Phase 1 steps above (pipeline, UI, ScoreComputer, most of invariants I1–I12).
