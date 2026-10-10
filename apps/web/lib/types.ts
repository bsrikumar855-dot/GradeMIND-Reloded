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
