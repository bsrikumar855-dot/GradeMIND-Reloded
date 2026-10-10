/** Shapes of the API's grading documents (mirrors grademind_core.grading; marks are decimal strings). */

export type PaperNode = {
  id: string;
  label: string;
  text?: string;
  max_marks?: string | null;
  choose?: number | null;
  children?: PaperNode[];
};
export type Paper = { total_marks: string; questions: PaperNode[] };

export type Level = { id: string; name: string; marks: string; definition: string };
export type Criterion = { id: string; name: string; description?: string; levels: Level[] };
export type QuestionRubric = { qid: string; criteria: Criterion[] };
export type Rubric = { questions: QuestionRubric[] };

export type Policy = {
  or_selection: "best" | "first_attempted";
  multiple_attempts: "last" | "first" | "best";
  rounding_mode: "none" | "half_up" | "up" | "down";
  rounding_step: string;
  rounding_scope: "question" | "total";
  negative_marking: boolean;
  negative_floor: "question" | "total";
};

export const DEFAULT_POLICY: Policy = {
  or_selection: "best",
  multiple_attempts: "last",
  rounding_mode: "none",
  rounding_step: "0.5",
  rounding_scope: "total",
  negative_marking: false,
  negative_floor: "total",
};

export type Version<T> = { id: string; version_no: number; status: "DRAFT" | "APPROVED"; document: T; policy?: Policy | null; created_at: string; approved_at: string | null };

export function leaves(nodes: PaperNode[]): PaperNode[] {
  return nodes.flatMap((n) => (n.children && n.children.length ? leaves(n.children) : [n]));
}

/** Human label of a node including its ancestors, e.g. "2 (a) (i)". */
export function fullLabels(nodes: PaperNode[], prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  for (const n of nodes) {
    const label = n.label.startsWith("Section") ? n.label : `${prefix}${prefix ? " " : ""}${n.label}`.trim();
    out.set(n.id, label);
    if (n.children) for (const [k, v] of fullLabels(n.children, n.label.startsWith("Section") ? "" : label)) out.set(k, v);
  }
  return out;
}

/** Submissions, pages and the grading workspace (mirrors the API's routes/submissions.py and routes/grading.py). */
export type SubmissionRow = {
  id: string;
  exam_id: string;
  student_ref: string;
  consent_scope: "local_only" | "public_release";
  mime: string;
  size_bytes: number;
  filename: string;
  created_at: string;
  job_id: string | null;
  job_status: string | null;
  job_stage: string | null;
  job_error: string | null;
  /** "ingest", or "ocr" / "ocr_retry" once the machine reading is the latest job */
  job_kind: string | null;
  /** automatic retries of the machine reading so far (bounded) */
  job_retry_count: number;
  page_count: number;
  /** every page image is rendered: grading can start, whatever the rest of the job (machine reading) is doing */
  pages_ready: boolean;
  /** the result is frozen in a snapshot (read-only until reopened) */
  finalized: boolean;
};

export type OcrSummary = { submission_id: string; pages: number; pages_read: number; pages_failed: number };

export type PageInfo = { id: string; page_no: number; width: number; height: number; image_url: string; thumb_url: string | null };
export type Region = { id: string; page_id: string; bbox: [number, number, number, number]; qid: string; attempt_no: number; crossed_out: boolean };
export type EvaluationInfo = {
  id: string;
  qid: string;
  attempt_no: number;
  verdicts: Record<string, string>;
  notes: string;
  evidence_region_id: string | null;
  marks: string;
  examiner_id: string;
  is_override: boolean;
  override_reason: string | null;
  created_at: string;
};
export type ScoreInfo = {
  total: string;
  max_total: string;
  complete: boolean;
  nodes: Record<string, { marks?: string; max?: string; [k: string]: unknown }>;
  flags: string[];
  version: string;
};
export type WorkspaceData = {
  submission_id: string;
  exam_id: string;
  student_ref: string;
  paper: Paper;
  rubric: Rubric;
  policy: Policy;
  pages: PageInfo[];
  regions: Region[];
  evaluations: EvaluationInfo[];
  score: ScoreInfo;
  finalization: { state: "OPEN" | "FINALIZED"; snapshot_no: number | null };
};
export type TotalsData = {
  columns: { id: string; label: string; max: string }[];
  rows: { submission_id: string; student_ref: string; total: string | null; max_total: string; complete: boolean; sections: Record<string, string>; flags: string[]; finalized: boolean; snapshot_no: number | null }[];
};
export type AuditRow = { at: string; actor_id: string | null; action: string; entity_type: string; entity_id: string | null; details: Record<string, unknown> };

/** Machine reading (OCR assist, D28). DISPLAY-ONLY: nothing here may feed a verdict. Mirrors routes/ocr.py. */
export type MachineLine = {
  id: string;
  text: string; // what to show: the examiner's correction if there is one. Student text: DATA, render through safeText() only
  original_text: string; // what the machine read; never overwritten
  corrected: boolean;
  correction_id: string | null; // the current correction: send it back as expected_correction_id when editing again
  score: number | null;
  low_confidence: boolean;
  bbox: [number, number, number, number]; // page fractions, the same space as answer regions
  overlap: number;
};
export type RegionReading = {
  region_id: string;
  qid: string;
  attempt_no: number;
  crossed_out: boolean;
  page_id: string;
  page_no: number;
  page_status: "read" | "failed" | "unread";
  lines: MachineLine[];
};
export type MachineReadingData = { notice: string; low_confidence_below: number; regions: RegionReading[] };
export type LineHighlight = { page_id: string; bbox: [number, number, number, number] };

export type UserRow = {
  id: string;
  email: string;
  display_name: string;
  role: "admin" | "teacher" | "examiner";
  is_active: boolean;
  created_at: string;
};

export type FinalizationIssue = { code: string; message: string };
export type FinalizationData = {
  state: "OPEN" | "FINALIZED";
  snapshot: {
    id: string;
    snapshot_no: number;
    rubric_version_no: number;
    paper_version_no: number;
    score_computer_version: string;
    total: string;
    max_total: string;
    finalized_by: string;
    finalized_at: string;
  } | null;
  ready: boolean;
  blockers: FinalizationIssue[];
  not_attempted: { qid: string; label: string }[];
  history: { action: "FINALIZED" | "REOPENED"; at: string; by: string; reason: string | null; snapshot_no: number }[];
};

export type AnalyticsLevel = { id: string; name: string; marks: string; count: number; share: string | null };
export type AnalyticsQuestion = {
  qid: string;
  label: string;
  max_marks: string;
  n: number;
  suppressed: boolean;
  mean: string | null;
  median: string | null;
  not_attempted: number;
  incomplete: number;
  distribution: { marks: string; count: number; share: string | null }[];
  criteria: { id: string; name: string; n: number; suppressed: boolean; levels: AnalyticsLevel[] }[];
};
export type AnalyticsData = {
  scope: "all" | "finalized";
  min_n: number;
  rubric_version_no: number;
  score_computer_version: string;
  booklets: { total: number; finalized: number; not_started: number; in_progress: number; complete: number };
  ungraded: { mapped_but_ungraded_answers: number; booklets_with_ungraded: number };
  questions: AnalyticsQuestion[];
  overrides: { grades_saved: number; overrides: number; rate: string | null; suppressed: boolean };
  time_to_grade: { n: number; suppressed: boolean; median_seconds: number | null; note: string };
  definitions: string[];
};
