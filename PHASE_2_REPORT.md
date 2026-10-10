# PHASE 2 REPORT: grading core (D24 / D25)

Status: **ready for owner review. Stopped at the phase gate; Phase 3 has not been started.**
Source of truth for test results is CI (rule 13). Final head: `6d0f7d6`, CI run `38023217004`: jobs `test`, `secrets`, `web`, `compose-smoke` all green.

## What an examiner can do now (D25 scope)

| D25 item | Where | State |
|---|---|---|
| a. Question paper: PDF text-layer or pasted text, rule-based parse (no LLM), edit UI, approve | `/exams/{id}/paper`, `core/paper_parser.py` | Implemented and tested (steps 2.2, 2.3, 2.6) |
| b. Rubric editor, DRAFT -> APPROVED immutable versions, policy snapshot per version | `/exams/{id}/rubric`, DB trigger on approved rows | Implemented and tested (2.3, 2.6) |
| c. ScoreComputer: pure, Decimal, versioned | `core/grading.py` | Implemented and tested: 100% branch coverage gate + Hypothesis (2.1) |
| d. Booklet upload -> page images -> viewer (zoom, pan, rotate, thumbnails, fit width/page) | `/exams/{id}/submissions`, `/submissions/{id}`, RASTERIZE stage | Implemented; viewer is **tested only through the E2E happy path** (2.4, 2.6) |
| e. Manual region mapping (draw a box, assign to a question/attempt, crossed-out, remove) | workspace | Implemented; draw + save in E2E, crossed-out/remove/new-attempt covered by API tests only (2.5, 2.6) |
| f. Grading workspace: verdict level per criterion, notes, override with reason, keyboard 1-9 / N / P / ? / Ctrl+Enter | workspace | Implemented; E2E covers radio pick, key `1`, Ctrl+Enter. N, P, ? are **untested** (2.5, 2.6) |
| g. Totals page + CSV (formula-injection guarded), exam audit trail | `/exams/{id}/totals`, `/audit` | Implemented and tested (2.5, 2.6) |
| Playwright E2E | `apps/web/e2e/phase2.spec.ts`, CI job `compose-smoke` | Passing in CI (2.7) |

No OCR output is used anywhere in grading (D19). Nothing is sent to any cloud service.

## End-to-end evidence (CI run `38023217004`, real stack: Postgres, Redis, MinIO, API, Celery worker, web, Chrome)

One test drives the real UI, as the bootstrapped admin: create exam (5 marks) -> paste a 2-question paper, parse, save, approve -> approve the default rubric ->
upload a 2-page PDF booklet and wait for the worker to render it -> open the workspace -> draw an answer box on Q1, pick "Full", save (`2 / 2`, total `2 / 5`) ->
draw on Q2, grade with the keyboard (key `1` = None, Ctrl+Enter) (`0 / 3`) -> totals page shows the row; CSV row matches `E2E-001,...,2,5,yes` -> audit page.

Audit query output (the audit tab's rows, oldest first, published by the test as a job annotation):

```
exam.create | paper.draft_save | paper.approve | rubric.draft_save | rubric.approve | submission.create |
region.create | evaluation.save | region.create | evaluation.save | totals.export_csv
```

Two `region.create`, two `evaluation.save`, each written in the same transaction as the recomputed score sheet.

## Problems found and fixed on the way

- CI was red on the WIP commit `89ee9fe`: two `react-hooks/set-state-in-effect` lint errors in the editors and one Next lint warning. The web image build runs eslint with `--max-warnings 0`, so this failed both `web` and `compose-smoke`. Fixed in `a98fbb7`. An earlier suspicion that the OCR vendoring step was at fault was wrong (that step succeeded).
- E2E bring-up took four CI iterations, all test/CI wiring, none product bugs: `corepack` absent on the runner; sourcing `.env` as shell failed on a value with a space; the test's "Grade" link matched the header brand "GradeMIND".

## Known gaps (not hidden)

- E2E uses the admin role only. The examiner role, the **override-with-reason** path and the history endpoint are covered by API tests (`apps/api/tests/test_api_grading.py`), not by the browser test.
- Not browser-tested: rotate, fit page, thumbnails, drag-to-pan, crossed-out, remove region, attempts, N/P/? shortcuts, retry of a failed ingest.
- Drawing is disabled while the page is rotated (the box would be stored in the wrong frame). Touch devices are untested.
- No automated accessibility audit; labels, roles and focus rings are by construction. The page canvas is mouse/pointer only: there is no keyboard way to draw a box yet.
- No web unit tests; the web is covered by lint, types, build and the one E2E.
- Booklet page images come from PDFs/images only. The E2E uses a synthetic 2-page PDF, not a real answer sheet. Real handwriting has not gone through this UI.
- The lease heartbeat for stages longer than `job_lease_seconds` is still pending the OCR stage (Phase 3), as noted in STATUS.
- Stuck `QUEUED` jobs (broker down at upload) are not re-sent automatically.

## Decisions needed from the owner

1. Approve Phase 2 as is, or list changes (most likely candidates: keyboard-only region drawing; more browser coverage of override/attempts).
2. Phase 3 (document intelligence) scope confirmation per D24. Per D19, OCR stays assistive; examiner line corrections are stored as labelled data.
3. Owner-side blockers are unchanged and independent of this phase: OWNER_VERIFIED transcriptions (gates Phase 0c), D20 public-release consent for sheets 001/002, more sheets for D23 (>= 10 students, >= 2 subjects, >= 1 numerical).
