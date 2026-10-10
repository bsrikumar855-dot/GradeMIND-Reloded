# GradeMIND v2: Implementation Plan (after the Phase 1 review)

The phases and gates are those of spec §21, re-scoped by the owner decisions D18–D27 ([DECISIONS.md](DECISIONS.md)). Every phase ends with
`PHASE_<n>_REPORT.md` and a STOP. Phase 0c and Phase 1 run **in parallel**, and **Phase 1 must not depend on Phase 0c**.

## Phase 0 / 0b: done

Reports: [PHASE_0_REPORT.md](../PHASE_0_REPORT.md), [PHASE_0B_REPORT.md](../PHASE_0B_REPORT.md). Outcome: D18 (OCR defaults) and D19 (v1 scope).

## Phase 0c: cloud ceiling reference (benchmark only, D22)

1. Prompt `prompts/ocr_ceiling/v1.md`, runner `spike/ceiling/run_ceiling.py`, and the **pre-registered decision rule** in
   `docs/PHASE_0C_PLAN.md`, all committed **before any data is sent**.
2. **Gate:** the runner refuses to send anything unless **both** transcription manifests are `OWNER_VERIFIED` (D21), the owner has confirmed this in chat,
   and the API keys are present in the environment. **Nothing is sent before that.**
3. Run: the 25 redacted answer pages, top Gemini + top Claude vision models (IDs resolved and logged at runtime, rule 12), temperature 0, **2 runs per page per model**.
   Every request is logged (model, parameters, prompt hash, image sha256).
4. Score with the report_0b tooling (CER/WER, autocorrection, omissions, labels, digits, intervals). Apply the decision rule. Write `PHASE_0C_REPORT.md`. **STOP.**

## Phase 1: Foundation (scoped by D19): done, approved

Report: [PHASE_1_REPORT.md](../PHASE_1_REPORT.md). The steps below are kept as a record.

Order, with each step a small, separately tested commit:

1. **Monorepo layout:** `apps/api` (FastAPI), `apps/worker` (Celery), `apps/web` (Next.js), `services/ocr` (PP-OCRv6, CPU), `packages/*` shared Python; uv workspace; pnpm for web
   (in Docker; the host has Node 18 and no pnpm).
2. **Single config source:** pydantic-settings. Flags: `AI_SUGGESTIONS_ENABLED=false`, the OCR provider registry (`paddle_v6` on; `trocr_line`, `unlimited_ocr` off),
   the LLM gateway kill switch. **Tests:** a disabled provider is never called (rule 7); the app refuses to start with a fake provider unless `GRADEMIND_ENV=test` (I11).
3. **Docker Compose:** Postgres 16, Redis, MinIO, api, worker, ocr, web; health checks; `docker compose up` → health-green.
4. **DB + Alembic:** the core schema (orgs, users, roles, exams, papers, questions, rubrics, submissions, pages, page flags, OCR runs and lines, **line corrections (§5 of
   ARCHITECTURE)**, jobs, stage attempts, audit log). UUID keys, append-only tables for the I8 entities.
5. **Auth + RBAC:** admin, examiner, teacher; examiners see only their assigned exams; API tests for every denial.
6. **Storage:** MinIO via a single storage module; UUID object keys; upload validation (MIME sniffing, size, sanitisation); short-lived signed URLs.
7. **Jobs + SSE:** the stage machine with idempotent, resumable stages; `/api/jobs/{id}/events` SSE; retry of the failed stage only.
8. **Web shell:** Next.js App Router, TS strict, Tailwind, shadcn/ui; login, dashboard, exam list. Accessible (WCAG AA), light theme.
9. **CI:** ruff, mypy, tsc, import-linter (provider SDKs only in their adapters), `scripts/check_single_paths.sh`, pytest, alembic upgrade check,
   compose smoke. `make test` remains the single entry point (rule 13).

**Exit (spec):** `docker compose up` gives a health-green stack, and the CI log shows all checks passing. Then `PHASE_1_REPORT.md` and **STOP**.

## Phase 1 hardening (D26): before any Phase 2 work

1. **Job lease heartbeats.** A running stage renews its lease periodically; a lease is reclaimed only after N missed
   heartbeats. Tests: a stage longer than the lease window is not taken by a second worker; a killed worker's job is
   reclaimed. Stage outputs carry an idempotency key, so a duplicate run cannot double-write.
2. **Supply chain.** Every image pinned by digest; Paddle CPU wheels cached/vendored so a Paddle CDN outage cannot break CI.
3. **Secret hygiene.** Secret scanning in CI; a test that `.env` and key files are untracked.
4. **Log redaction.** Regression test for the password-in-logs bug; redaction test for token / password / Authorization.
5. **D19 schema check.** Show the append-only line-correction table and its migration.
6. **README.** Docker-group warning (root-equivalent) and the `sg docker -c "make smoke"` workaround.

## Phase 2: grading core vertical slice, no OCR dependency (D24, D25)

"An examiner grades a booklet end to end." Small, separately tested commits, in this order:

1. **Question paper:** upload; structure tree built manually or by deterministic parsing (Q1 / 1(a) / (i), marks, OR
   groups); examiner edit UI. No LLM parsing (D19).
2. **Rubric:** criteria, named verdict levels with definitions; validation on save; draft → APPROVED immutable versions (I5, I8).
3. **ScoreComputer:** pure, Decimal, versioned; 100% branch coverage + Hypothesis properties (total ≤ max, monotonic,
   permutation-invariant, idempotent); policy: OR choice, multiple attempts, rounding, negative marking.
4. **Booklet:** upload → page images → viewer (zoom, pan, rotate, thumbnails, fit width/page). No OCR calls.
5. **Manual mapping:** the examiner draws a box on a page and assigns it to a question (region model).
6. **Review workspace:** verdict level per criterion, notes, optional evidence region, keyboard shortcuts (1–9, N/P, ?);
   ScoreComputer computes; immutable evaluations, overrides, audit log.
7. **Totals:** section and total marks view; CSV export.
8. **Exit:** Playwright E2E (upload paper → approve rubric → upload booklet → map answers → grade → totals → audit rows)
   passing in CI, plus the audit-table query output. Then `PHASE_2_REPORT.md` and **STOP**.

## Phase 3: document intelligence (OCR assist), D28

Assistive only: OCR text is display-only data, never an input to a verdict or to ScoreComputer (import-linter contract).
One commit per step, CI green before the next, STATUS.md updated each time:

- **3.0** Phase 2 gaps: keyboard-only answer-box drawing; Playwright for override-with-reason (examiner), attempts, crossed-out, remove region, N/P/?; stuck-QUEUED sweeper test.
- **3.1** OCR stage in the ingest job: per-page `/ocr/page`, append-only lines table keyed by page + engine config hash, heartbeats, idempotent, retry; a failed OCR page never blocks grading.
- **3.2** Region text API: lines inside an answer region in reading order (pure geometry, unit-tested).
- **3.3** "Machine reading" panel: low-confidence lines flagged, "may be wrong", hide toggle, keyboard accessible.
- **3.4** Examiner line correction -> `line_corrections` (append-only, audit in the same transaction, original one click away).
- **3.5** Owner-only labelled-dataset export; `consent_scope = local_only` rows never leave the machine.
- **3.6** E2E: upload, machine lines in the region, correct one, corrections + audit rows, totals unchanged by OCR presence.
- Exit: CI green; `PHASE_3_REPORT.md` (states plainly: not validated on real handwriting); STOP.

## Phase 4: review polish, analytics, reports

Review queue polish, examiner and student reports, score distribution, per-question difficulty, override rate.

## Blocking data requirement (D23)

**No AI-suggestion feature ships** until the benchmark covers ≥ 10 students, ≥ 2 subjects, and ≥ 1 numerical subject. Current coverage is 2 students and 1 subject (non-numerical).
