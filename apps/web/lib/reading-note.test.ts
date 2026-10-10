import assert from "node:assert/strict";
import { test } from "node:test";
import { readingNote } from "./reading-note.ts";
import type { OcrSummary, SubmissionRow } from "./types.ts";

const row = (over: Partial<SubmissionRow>): SubmissionRow =>
  ({
    id: "s",
    job_id: "j",
    job_kind: "ocr",
    job_status: "COMPLETED",
    job_stage: "OCR",
    job_error: null,
    job_retry_count: 0,
    page_count: 3,
    pages_ready: true,
    ...over,
  }) as SubmissionRow;
const sum = (read: number, failed = 0): OcrSummary => ({ submission_id: "s", pages: 3, pages_read: read, pages_failed: failed });

test("before the pages exist there is nothing to say", () => {
  assert.equal(readingNote(row({ pages_ready: false, job_kind: "ingest", job_status: "RUNNING" }), undefined), "—");
});

test("a finished reading says how many pages were read", () => {
  assert.equal(readingNote(row({}), sum(3)), "3 of 3 pages read");
  assert.equal(readingNote(row({}), sum(2, 1)), "2 of 3 pages read, 1 could not be read");
});

test("while the service is being retried by itself the examiner is told so, with the retry number", () => {
  assert.match(readingNote(row({ job_status: "QUEUED", job_retry_count: 2 }), sum(1)), /Trying again by itself \(retry 2\)/);
  assert.equal(readingNote(row({ job_status: "QUEUED", job_retry_count: 0 }), sum(0)), "Waiting to be read");
  assert.equal(readingNote(row({ job_status: "RUNNING" }), sum(0)), "Reading the pages…");
});

test("after the last retry the booklet is plainly unread, and grading is said to be unaffected", () => {
  const t = readingNote(row({ job_status: "FAILED", job_retry_count: 4 }), sum(0));
  assert.match(t, /^Unread:/);
  assert.match(t, /Grading is not affected/);
  assert.match(readingNote(row({ job_status: "FAILED" }), sum(1)), /1 of 3 pages read/);
});

test("an ingest job's status is not a statement about the machine reading", () => {
  assert.equal(readingNote(row({ job_kind: "ingest", job_status: "COMPLETED" }), sum(0)), "0 of 3 pages read");
});
