# GradeMIND v2: Implementation Plan (after the Phase 0b review)

The phases and gates are those of spec §21, re-scoped by the owner decisions D18–D23 ([DECISIONS.md](DECISIONS.md)). Every phase ends with
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

## Phase 1: Foundation (scoped by D19)

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

## Phase 2+ (after Phase 1 approval)

As in spec §21, re-scoped by D19:
- **Phase 2** (document intelligence): ingest, characterisation, OCR provider, examiner label confirmation, alignment, correction capture.
- **Phase 3:** rubric + ScoreComputer + examiner verdict entry; AI suggestions stay disabled.
- **Phase 4:** review workspace.
- **Phase 5:** hardening, seeded demo.

D3 resilience (OCR crash/resume integration test) belongs in Phase 2.

## Blocking data requirement (D23)

**No AI-suggestion feature ships** until the benchmark covers ≥ 10 students, ≥ 2 subjects, and ≥ 1 numerical subject. Current coverage is 2 students and 1 subject (non-numerical).
