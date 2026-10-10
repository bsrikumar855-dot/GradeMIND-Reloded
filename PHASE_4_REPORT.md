# PHASE 4 REPORT: review polish, analytics, reports, pilot readiness (D24 / D29)

Status: **ready for owner review. Stopped at the phase gate; Phase 5 and any cloud work have not been started.**
Source of truth for test results is CI (rule 13). Last code commit: `ea0e4b6`, CI run `38058190060`: jobs `test`, `secrets`, `web`, `compose-smoke` all green
(28 browser tests, 407 Python tests, 25 web unit tests, plus the new CI checks listed below). The evidence quoted below is from run `38056642051` on `3207d0d` (the same code
except for one healthcheck setting: see "Problems found").
(This report and the STATUS update are a docs-only commit after it; its CI result is stated in the hand-over message, not here.)

**Read this first: handwriting reading is still unvalidated.** Phase 4 added no handwritten data and no new measurement. Phase 0b measured a best line error of about 0.43
on real handwritten sheets (7 pages, ground truth `AGENT_VERIFIED`); the machine reading has only ever been exercised end to end on a synthetic printed page. The product
treats every machine line as "can be wrong" and never lets it near a mark. The 0.80 low-confidence flag is still a labelled placeholder (D29.4: calibrate only after at least
200 real corrections; the corrections table holds E2E test rows only). Nothing in this phase is evidence that the reading is good enough to save an examiner time.

## What works

| Step | What | Commit, CI run (all four jobs green unless noted) |
|---|---|---|
| 4.0 | Machine reading is its own job on its own queue and worker (`ocr-worker`, concurrency 1); an unavailable service is retried by itself (4 times, 30 s / 2 min / 8 min / 15 min, persisted so a restart loses nothing), then "Unread" with a manual "Retry machine reading" button; the sweeper also re-queues failed readings and creates a reading job that a crashed worker never made; one active reading per booklet (unique index). | `48f1922` red (`38046106208`: a test race in the audit read), `bf387bd` green `38046363766` |
| 4.1 | Admin UI: create/deactivate users, assign examiners; role checks tested (an examiner sees only assigned exams and no audit, users or examiners pages; the API refuses what the UI hides). | `2e03fd8` `38047150943` |
| 4.2 | Finalize and reopen: append-only result snapshots (rubric and paper version, ScoreComputer version, policy, attempts, which evaluations, an input digest, per-question marks, who/when); read-only while final, in the API **and in the database** (triggers; a finalize and a grading write in flight cannot interleave); reopen needs a reason and is audited; `verify-snapshots` recomputes every snapshot from the raw grades and fails loudly. | `b079515` `38048282657` |
| 4.3 | Analytics from verdicts only: per-question mark distribution, per-criterion level counts, override rate, ungraded and not-attempted counts, time to first grade. Every figure shows n; with n < 5 the mean, median and every percentage are withheld. A test shows OCR cannot change a number. | `2309f47` `38049209701` |
| 4.4 | Reports built only from finalized snapshots and stating them: student result sheet (PDF), exam summary (CSV, PDF); examiner notes are never printed; formula-injection guard kept; every generation audited with the snapshot id and the SHA-256 of the output. A small dependency-free PDF writer, read back with a real PDF reader in tests; a sample sheet was rendered and looked at. | `d42e36f` `38050428355` |
| 4.5 | axe (WCAG 2.0/2.1/2.2 A and AA) on every main page and workspace state, as administrator and examiner, desktop and 375 px; a keyboard-only run of the whole grading flow (the page records any real pointer press); real touch drawing; fixed what was found. | `d2b511c` `38053222820` |
| 4.6 | Backup and restore (database + every object, SHA-256 throughout) with a CI drill; one-page operator runbook; security pass: sign-in throttling, constant-cost miss, per-request-nonce CSP, API headers, API docs off in production, dependency audit. | `d058f50` never started (workflow YAML invalid), `f96fbeb` green `38055097962` |
| 4.7 | The pilot flow end to end in CI, nothing mocked, with printed evidence. | `27eb120` red `38055904005` (the drill caught a backup flaw), `3207d0d` green `38056642051`; the docs commit after it went red on an OCR healthcheck flake, hardened in `ea0e4b6` green `38058190060` |

Invariants held: the examiner stays the final authority (no AI verdicts, no auto-grading); no OCR text in any mark computation (the import contract now also covers the finalize,
snapshot, analytics, report and PDF modules, each with a planted-import test, and passes); append-only records (finalizing never deletes or edits history; the database refuses it);
nothing leaves the machine.

## End-to-end evidence (CI run `38056642051`, real stack: Postgres, Redis, MinIO, API, workers, web, OCR, Chrome)

`phase4.spec.ts` (22.9 s): an administrator creates two examiners and a teacher and assigns the examiners; Alice grades booklet 1 in the UI (5 / 5); Bob grades booklet 2 (1 / 5) and **overrides**
Alice's Q1 on booklet 1 (refused without a reason, accepted with one: 4 / 5); an examiner cannot finalize (403), a teacher cannot create users (403); the teacher **finalizes** both booklets;
the result sheet and the summary CSV carry the snapshot id; analytics withholds the mean for two booklets and counts the override; the teacher **reopens** booklet 1 with a reason (no sheet while
open: 409), Alice changes Q2, the teacher **finalizes again** (snapshot 2); the new sheet names snapshot 2 and not snapshot 1.

The exam's audit trail, as the page shows it (oldest first):
`exam.create | paper.draft_save | paper.approve | rubric.draft_save | rubric.approve | exam.assign | exam.assign | submission.create | submission.create | region.create | evaluation.save | region.create | evaluation.save | region.create | evaluation.save | region.create | evaluation.save | evaluation.override | submission.finalize | submission.finalize | report.student_pdf | report.summary_csv | submission.reopen | evaluation.save | submission.finalize | report.student_pdf | report.summary_pdf`

`scripts/phase4_evidence.sh` against the live database: `P4-A: FINALIZED (snapshot 1) -> REOPENED (snapshot 1) -> FINALIZED (snapshot 2)`; snapshots `P4-A #1 4/5`, `P4-A #2 1/5`, `P4-B #1 1/5`
(rubric version 1, `score-computer-1.0.0`); no examiner ever finalized anything; each student report's audit row carries the snapshot number and the SHA-256 of the exact bytes; the database refused
7 of 7 attempted edits (update/delete of snapshots and events; update/delete of a finalized booklet's regions; a new evaluation for a finalized booklet). Final line `PHASE 4 EVIDENCE OK`.

`verify-snapshots` on that data: `VERIFY OK: 7 snapshot(s) recomputed from the raw records; every one reproduces exactly`.

**Backup and restore drill** on that data: back up (101 objects, schema 0012) -> restore a side copy: `RESTORE VERIFY OK: schema 0012; 61 stored object(s) match their recorded SHA-256; 7 result snapshot(s) recompute exactly`, row counts
equal -> stop the services -> restore over the live data (`--replace-live`) -> `RESTORE VERIFY OK` again -> restart -> `VERIFY OK: 7 snapshot(s)`; the administrator signs in; the restored system lists its 15 exams.

Also in CI: the OCR service killed in the middle of a 4-page booklet is retried by itself and every page is read exactly once (step 4.0); `PASS` on the CSP, nosniff, framing, referrer and API-docs checks against the running stack;
the dependency audit prints as a report.

## What is tested and what is not

Tested in CI: everything in the table above, at the level described. Counts: 407 Python tests (the grading, scoring and OCR-geometry modules keep their 100 % branch coverage gate), 28 browser tests, 25 web unit tests.

**First runs happened in CI, not here.** This host's Docker daemon refuses to stop or kill containers (AppArmor, needs root), so I could not run the compose stack, the backup and restore scripts, or the OCR kill/restart
test locally. They were checked with shellcheck, a YAML parser, a local simulation of the snapshot dump against a live-changing database, and then run for the first time in CI. That is why several of the red runs below exist.

## Problems found on the way (all fixed; the red runs are listed, not hidden)

- `48f1922` red: the Phase 3 browser test read the audit table before its rows loaded (a test race; the page was fine). Fixed in `bf387bd`.
- `d058f50` never ran: two step names in `ci.yml` contained `: `, so GitHub reported a workflow file error. Fixed (`f96fbeb`) and now checked with a YAML parser before pushing.
- `27eb120` red: **the restore drill caught a real flaw in my backup script.** It dumped the database and then counted the rows; the machine-reading worker finished a page in between, so the restore looked one row short.
  Fixed in `3207d0d`: one session exports a REPEATABLE READ snapshot, `pg_dump --snapshot` reads it, and the counts are taken in the same session. Verified against a database that was changing during the dump.
- `52c8b55` (docs only) red: after **both restores had printed `RESTORE VERIFY OK`**, `docker compose start` refused to start `ocr-worker` because the OCR container was marked unhealthy. Its image healthcheck gives `/ocr/health`
  5 s three times, and the service is CPU-bound while it reads a page on a 4-core runner shared with Chrome and the workers: the same family as the first Phase 3.6 failure. The identical code had passed one run earlier, so this was a flake,
  not a regression. Hardened in `ea0e4b6` (compose healthcheck: 25 s per request, 8 tries, about two minutes of sustained failure before "unhealthy"; the drill waits for OCR to be healthy before restarting its dependents). It is also
  an operator lesson: on a busy machine the OCR service can look unhealthy for a while without being broken (the runbook's OCR-down entry covers restarting it).
- 4.5 found real input problems: tables overflowing a phone screen, three scrollable tables unreachable by keyboard, small touch targets, and **a sideways finger drag on the page image triggering the browser's Back gesture**
  (confirmed: the navigation was `back_forward`; `overscroll-behavior` alone did not stop it; the viewer now handles sideways touch drags itself). The desktop pages passed axe on the first run.
- A first CSP test "failed" because I injected a script from trusted test code, which `strict-dynamic` allows by design; the real attack (injected markup with an inline handler) is blocked and tested.
- Test-side fixes: dropped a leftover `pkill -f` that killed my own shell; Playwright taps were unreliable in touch emulation (toggle buttons are clicked; the finger drag under test uses real touch events).

## Known gaps (not hidden)

- **Handwriting is unvalidated** (above). The first examiner corrections will be the first real data.
- **No password change or reset.** An administrator sets a temporary password when creating an account; users cannot change it, and there is no reset flow (the workaround is deactivate and re-create). This matters before a pilot.
- **Screen readers and real touch devices were not tested** (`docs/ACCESSIBILITY.md`): touch used Chrome's emulation on a desktop; nobody has walked the flow with NVDA/JAWS/VoiceOver/TalkBack.
- **PDF reports print only Windows-1252 characters** (other scripts become "?" and the audit row records it) and use a plain monospaced layout. No Indian-script support.
- **Rubric changes after grading started**: totals, analytics and the booklet list follow the latest approved rubric version; finalized results from an earlier version stay correct in snapshots and reports, but drop out of the live analytics. Not new, but now more visible.
- **Backups are not encrypted by this software**, are not scheduled (the operator runs them), and were drilled only on small data (101 objects); each object is read into memory (limit 50 MB). No off-site copy.
- **Sign-in throttling** trusts the `X-Forwarded-For` the web app receives; without a reverse proxy in front a caller who can reach the web port directly can pick that value (the account-wide limit still holds; default binding is `127.0.0.1`). No individual session revocation, no multi-factor sign-in.
- **No TLS in the stack** (a reverse proxy is described in the runbook). **Container images are not scanned**; `paddlepaddle` (vendored) cannot be audited through PyPI; the web audit shows one **high** advisory (`braces`, dev-only lint dependency, no patched version): accepted and documented (`docs/SECURITY_AUDIT.md`).
- **Analytics** report time to first grade as elapsed time (it includes time away); the override rate is overall, not per examiner (deliberately); counts are shown under n = 5, only derived numbers are withheld.
- **Machine reading**: a deep queue still gets one harmless duplicate message per sweep window; only "service unavailable" is retried automatically, not a page the service rejected; concurrency is 1 per OCR instance (about 12 to 14 s per page on this CPU).
- The old Docker stack on the developer machine could not be replaced (AppArmor); CI builds everything fresh.

## Decisions needed from the owner

1. **Approve Phase 4 as is, or list changes.**
2. **Pilot shape, given unvalidated handwriting reading.** Run the pilot with the machine reading visible (as built), hidden, or only for printed parts? What would "good enough" look like: the first 200 corrections give a measured error rate and calibrate the 0.80 flag.
3. **Password change and reset** before any pilot (a self-service change at least)?
4. **Backup policy**: schedule, where the encrypted copy lives, retention, who runs the monthly drill; whether to add encryption in the tool (a dependency decision) or rely on an encrypted disk.
5. **Network exposure and TLS**: who can reach the web port during the pilot; a reverse proxy with HTTPS and `GRADEMIND_COOKIE_SECURE=true`?
6. **Container image scanning** (which tool) and a base-image update routine.
7. **Who walks the flow with a screen reader, and on which real tablet or phone**, before the pilot.
8. **Report scripts**: do result sheets need Indian scripts? That needs a PDF library and fonts (a dependency and licence decision).
9. **A new rubric version after grading has started or results are final**: block it, carry results over, or leave as is.
10. **Examiner notes**: never printed today; should any appear on a student's sheet?
11. The minimum n of 5 for statistics: keep or change.
12. Owner-side items, unchanged and independent of this phase: `OWNER_VERIFIED` manifests (Phase 0c stays blocked, D27), the D20 consent records for sheets 001 and 002, data for D23 (at least 10 students, 2 subjects, 1 numerical).

I have not started Phase 5 and nothing was sent to any cloud service.
