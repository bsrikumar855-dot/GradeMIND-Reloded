import type { OcrSummary, SubmissionRow } from "./types.ts";

/** What the machine reading of a booklet is doing, in words an examiner needs. Grading never depends on it. */
export function readingNote(r: SubmissionRow, o: OcrSummary | undefined): string {
  if (!r.pages_ready) return "—";
  const summary =
    !o || o.pages === 0 ? "—" : `${o.pages_read} of ${o.pages} pages read${o.pages_failed ? `, ${o.pages_failed} could not be read` : ""}`;
  if (r.job_kind !== "ocr" && r.job_kind !== "ocr_retry") return summary;
  if (r.job_status === "RUNNING") return "Reading the pages…";
  if (r.job_status === "QUEUED")
    return r.job_retry_count > 0
      ? `The text-reading service was unavailable. Trying again by itself (retry ${r.job_retry_count}).`
      : "Waiting to be read";
  if (r.job_status === "FAILED")
    return `Unread: the text-reading service was not available. Grading is not affected.${o && o.pages_read > 0 ? ` ${summary}.` : ""}`;
  return summary;
}
