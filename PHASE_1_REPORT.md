# PHASE 1 REPORT: Foundation (scoped by D19)

**Status: STOP. Waiting for owner approval before Phase 2.**
Phase 1 does not depend on Phase 0c. Phase 0c remains prepared but blocked (see §8).

## 1. Exit criteria (spec §21)

| Criterion | Evidence |
|---|---|
| `docker compose up` runs a health-green stack | CI job `compose-smoke` (run [`37913482993`](https://github.com/bsrikumar855-dot/GradeMIND-Reloded/actions/runs/37913482993), commit `d3658ce`): `compose: all services healthy`, then `SMOKE OK`. Reproduced locally three ways: running stack, fresh volumes (`down -v`), and a **clean clone from GitHub** following the README. |
| The CI log shows all checks passing | Run `37913482993`: jobs `test`, `web`, `compose-smoke` all **success** (log lines in §3). |

## 2. What was built

| Step | Commit | CI | What |
|---|---|---|---|
| 1.1 | `4531421` | `37887515813` ✅ | uv workspace (`packages/core`, `apps/api`, `apps/worker`), single config source with startup invariants, provider registry |
| 1.2 | `b9ccf6d` | `37887812722` ✅ | Schema + Alembic baseline (orgs, users, exams, assignments, submissions, pages, page flags, OCR runs/lines, **line corrections** (D19), jobs, stage attempts, audit log). Postgres triggers make `line_corrections`, `audit_logs`, `job_stage_attempts` append-only (I8). Optimistic locking on jobs. |
| 1.3 | `969dd8a` | `37888752324` ✅ | argon2id + short-lived JWT; RBAC (admin / teacher / examiner; examiners see only assigned exams; out-of-scope = 404); one error envelope with `request_id`; JSON request logs; `/health`, `/health/db`, `/health/redis`, `/health/ocr`, `/health/models` |
| 1.4 | `75e6aea` | `37889396605` ✅ | Single storage module (MinIO), server-generated UUID keys, magic-byte sniffing + extension agreement, size limit enforced while streaming (ASGI middleware), filename sanitisation, short-lived signed URLs, duplicate-upload detection, `/health/storage` |
| 1.5 | `cb379e2` | `37889882133` ✅ | Stage machine: idempotent `FOR UPDATE` claim, lease-based resume, content-hash + component-version cache (retry re-runs only the failed stage and later ones), safe failure reasons; Celery task; `/api/jobs/{id}`, `/events` (SSE, `Last-Event-ID`), `/retry`; INTAKE stage (storage integrity) |
| 1.6 | `2a27784` | `37891158844` ❌ → fixed by `91765b6` | OCR service: PP-OCRv6 medium det+rec on CPU; **rule-12 assertion at image build and at startup** (library versions, model names, weight sha256, pipeline settings read back from the instantiated pipeline). The red run: lint referenced `scripts/`, which only landed in the next commit. |
| 1.7 | `91765b6` | `37891292406` ✅ | Docker Compose (postgres, redis, minio, migrate, bootstrap, api, worker, ocr), every image pinned by digest; allow-list `.dockerignore` (student data cannot enter a build context); bootstrap CLI; requeue sweep; end-to-end smoke script |
| 1.8 | `65d8591` | `37913136752` ✅ | Web shell: Next.js 16 App Router, TS strict, Tailwind 4, shadcn-style components; sign-in, dashboard (live health), exam list; session token only in an httpOnly cookie; CSRF origin check |
| 1.9 | `d3658ce` | `37913482993` ✅ | import-linter (6 contracts), `scripts/check_single_paths.sh` (spec rule 3), CI jobs `web` and `compose-smoke` |

## 3. Commands and outputs

All test numbers below come from `make test` / CI output (rule 13).

**CI run `37913482993` (commit `d3658ce`):**

```text
test          | 30 passed, 4 skipped in 4.84s                      (spike suite)
test          | 122 passed in 16.59s                               (app suite: real Postgres 16 + real MinIO)
test          | make test: spike pytest exit code = 0; app pytest exit code = 0
test          | All checks passed!                                 (ruff)
test          | Success: no issues found in 36 source files        (mypy --strict)
test          | Contracts: 6 kept, 0 broken.                       (import-linter)
test          | check_single_paths: 0 violation(s)
test          | No new upgrade operations detected.                (alembic check: models == migrations)
web           | ✓ Compiled successfully                            (after tsc --noEmit and eslint --max-warnings 0)
compose-smoke | rule 12 OK at build                                (fresh weight download on the GitHub runner hash-matches the Phase 0b pins)
compose-smoke | compose: all services healthy
compose-smoke | PASS /health/ocr -> 200 {"status": "ok", "ocr": {"status": "ok", "rule12": "OK", ...
compose-smoke | PASS web dashboard renders for the admin
compose-smoke | SMOKE OK
```

**What the smoke covers** (`scripts/compose_smoke.py`): every `/health/*` endpoint; admin sign-in; creating an exam; uploading a PDF; the ingest job run by the **real Celery worker** and followed over SSE to COMPLETED; the stage log; the signed URL returning the stored bytes; the web flow: redirect without a session, a wrong password refused, a cross-origin post refused (CSRF), the httpOnly cookie, the dashboard, the exam list, no token in the HTML, and redirects that stay on the web origin.

**Local evidence (this session):**
- OCR parity: the container's output on 3 pages (sheet_001 p02 and p05, sheet_002 p03; local originals, nothing sent anywhere) is **identical** to the Phase 0b `b_v6` output in text, boxes and scores. CPU latency is about 11–13 s per page.
- Rule 12, negative case: the real container with one tampered weight hash in the manifest prints `RULE 12 VIOLATION ... rec weights inference.pdiparams`, startup fails, exit 3, and it never serves a request.
- Shadow-path checks fire: 11 tests each plant one violation and require the scanner to report it (plus a clean-repo test and a false-positive test: 13 in all; the step 1.9 commit message said "13 tests plant one violation each", which overstated it); a planted `import minio` plus an api→worker import broke 2 import-linter contracts (the file was then removed).
- Clean clone: `git clone` → `cp .env` → `docker compose up -d --build` → `scripts/compose_wait.sh` → `scripts/compose_smoke.py`: all healthy, SMOKE OK.

## 4. Invariants and owner decisions now enforced by code and tests

| Item | Enforcement | Test |
|---|---|---|
| Rule 7 / I7: one config source; a disabled provider is never called | `Settings` validators; `ProviderRegistry` refuses before calling the factory | spy test: disabled factories are never invoked |
| I8: append-only history | Postgres triggers on 3 tables; corrections are new rows (`supersedes_id`) | UPDATE/DELETE raise; correction-of-correction is a new row |
| I10: auto-approve off until benchmarked | startup validator | config test |
| I11: fake providers only in tests | startup validator | config test |
| D18: Unlimited-OCR excluded; PP-OCRv6 primary; TrOCR behind a disabled flag | `EXCLUDED_IN_V1`; registry; OCR service manifest | config + registry tests; OCR rule-12 tests |
| D19: AI suggestions disabled | validator (requires an LLM provider and the kill switch off); **no LLM SDK** importable in the product path | config test; import-linter contract; scanner |
| Rule 12: resolved, not requested | OCR image build + startup assertion; `/health/ocr` exposes the resolved config | 12 unit tests + real-container negative test |
| Rule 3: no shadow paths | `scripts/check_single_paths.sh` in `make lint` and CI | 11 planted-violation tests + clean-repo + false-positive test |
| Spec §16 security | argon2id, JWT re-validated against the DB per request, RBAC 404s, upload sniffing/limits, UUID keys, signed URLs, secrets from the env only, placeholder secrets refused, httpOnly session, CSRF check | API tests for every denial; smoke |

## 5. Bugs found and fixed during Phase 1

1. **Password in logs:** the validation-error handler logged pydantic's `errors()`, which contains the offending input. For a malformed login body that included the password. Logs now keep only `loc/type/msg` (regression test).
2. **Mixed-case emails could not sign in.** Emails are now normalised on the model (regression test).
3. **Unbounded upload spooling:** Starlette writes the whole multipart body to disk before the endpoint runs. A streaming ASGI body limit now cuts oversized bodies off as they arrive (ASGI-level tests count the chunks consumed).
4. **Sign-in redirected the browser to `http://0.0.0.0:3000`** (the container bind address, which is another user's service on this host). Redirects are now relative; the smoke asserts it, failing on the old image and passing on the fixed one.
5. **Test isolation:** a leaked DB transaction blocked the next module's `alembic downgrade`.
6. **SSE ordering:** `created_at` cannot order rows (Postgres `now()` is the transaction start time), so a monotonic `seq` identity column is now the event id.
7. **Rule-12 honesty:** the first OCR manifest echoed our own pipeline arguments as "resolved". It now reads them back from the pipeline (including detection thresholds), and lists the one value that cannot be read back (`enable_mkldnn`) as `not_verifiable` instead of claiming it.

## 6. Known gaps (not done in Phase 1, by design or deferred)

- **No pipeline beyond INTAKE.** Preprocessing, the OCR stage, label confirmation, alignment and correction capture are Phase 2. The API does not call `/ocr/page` yet.
- **Lease renewal** happens only at stage boundaries; a stage longer than `job_lease_seconds` (900 s) could be resumed by a second worker. Heartbeats come with the OCR stage.
- **Orphaned objects:** a file stored before a failed DB commit is not swept.
- **MIME sniffing is magic bytes only**; deep PDF/image validation happens at ingest (Phase 2).
- **Web:** no Playwright tests yet (the spec's flows arrive in Phases 2–4). WCAG AA is by construction (labels, landmarks, skip link, focus rings, contrast-checked tokens), not by an automated audit.
- **Metrics:** structured JSON logs and health endpoints exist; no metrics endpoint yet.
- **Single worker** with embedded beat (`-B`); a multi-worker deployment needs a separate beat service.
- `data/README.md` consent entries for sheet_001/002 are still **NOT RECORDED** (D20).

## 7. Risks

| Risk | Impact | Mitigation in place / proposed |
|---|---|---|
| OCR on CPU is about 11–13 s per page | A 20-page booklet takes about 4 minutes, one booklet at a time | Inference is serialised so memory stays bounded. GPU use is possible later, within the ≤ 6 GB shared budget, if the owner wants it. |
| Paddle CPU wheels come from Paddle's CDN (PyPI stops at 3.3.1) | Slow or unavailable downloads break image builds | Versions pinned plus a lockfile; build-time rule 12 catches any weight drift. Proposal: mirror the wheel and weights as release artifacts. |
| MinIO community images left Docker Hub; we use Chainguard's free `:latest`, pinned by digest | The free tier may stop serving old digests | Pinned and documented; can move to a self-built image if the digest disappears. |
| Pre-release-adjacent toolchain (TypeScript 6.0 chosen over 7.0; Next 16; pnpm 12) | Upgrade friction | Exact pins, frozen lockfile, type check and lint inside the image build. |
| Shared host | Port 3000 is taken by another service; another user's process holds GPU memory | Web on 3100 (configurable); everything published on 127.0.0.1 only; no GPU use in Phase 1. |

## 8. Status of the parallel work and blockers (unchanged by Phase 1)

- **Phase 0c:** prepared (plan pre-registered at `5911e5c`, runner and report generator gated). **Nothing has been sent.** It needs both transcription manifests to be `OWNER_VERIFIED` and the owner's confirmation in chat (D21, D22).
- **D23:** no AI-suggestion feature ships before ≥ 10 students, ≥ 2 subjects and ≥ 1 numerical subject. Current: 2 students, 1 subject.
- **D20:** public-release consent for sheet_001/002 is not recorded.

## 9. Proposed next phase (for approval, not started)

Phase 2 (document intelligence, re-scoped by D19): PDF → page images at ingest; page characterisation plus PAGE_DETECTION_ANOMALY; the OCR stage calling the OCR service through the provider registry (with lease heartbeats); per-page examiner label confirmation (the P0 alignment strategy); line-level OCR correction capture into `line_corrections`; and the D3 crash/resume integration test.

**STOP.**
