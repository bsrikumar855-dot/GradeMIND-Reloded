"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { IssueList } from "@/components/grading/issues";
import { api, ClientError, type Issue } from "@/lib/client";
import { DEFAULT_POLICY, fullLabels, leaves, type Criterion, type Paper, type Policy, type QuestionRubric, type Rubric, type Version } from "@/lib/types";

type State = { paper: Version<Paper> | null; draft: Version<Rubric> | null; approved: Version<Rubric> | null; issues: Issue[]; stale_draft: boolean };

function newId(prefix: string): string {
  return prefix + Math.random().toString(36).slice(2, 7);
}

function defaultCriterion(max: string): Criterion {
  return {
    id: "c1",
    name: "Answer",
    description: "",
    levels: [
      { id: "none", name: "None", marks: "0", definition: "" },
      { id: "full", name: "Full", marks: max || "1", definition: "" },
    ],
  };
}

function CriterionEditor({
  qid,
  c,
  issues,
  readOnly,
  onChange,
}: {
  qid: string;
  c: Criterion;
  issues: Issue[];
  readOnly: boolean;
  onChange: (c: Criterion | null) => void;
}) {
  const base = `${qid}-${c.id}`;
  const mine = issues.filter((i) => i.path.startsWith(`rubric/${qid}/${c.id}`));
  return (
    <fieldset className={`rounded-md border p-3 ${mine.length ? "border-destructive/60" : ""}`}>
      <legend className="px-1 text-sm font-medium">Criterion</legend>
      <div className="flex flex-wrap items-end gap-2">
        <div className="flex min-w-48 flex-1 flex-col gap-1">
          <Label htmlFor={`${base}-name`} className="text-xs">
            Name
          </Label>
          <Input id={`${base}-name`} value={c.name} disabled={readOnly} onChange={(e) => onChange({ ...c, name: e.target.value })} />
        </div>
        {!readOnly ? (
          <Button type="button" variant="ghost" size="sm" aria-label={`Remove criterion ${c.name}`} onClick={() => onChange(null)}>
            <Trash2 aria-hidden="true" />
          </Button>
        ) : null}
      </div>
      <table className="mt-2 w-full text-sm">
        <caption className="sr-only">Levels of {c.name}, from lowest to highest marks</caption>
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th scope="col" className="py-1 pr-2 font-medium">Level</th>
            <th scope="col" className="w-24 py-1 pr-2 font-medium">Marks</th>
            <th scope="col" className="py-1 pr-2 font-medium">When it applies (required for partial levels)</th>
            <th scope="col" className="w-10" />
          </tr>
        </thead>
        <tbody>
          {c.levels.map((lv, i) => (
            <tr key={lv.id}>
              <td className="py-1 pr-2">
                <Input aria-label={`Level ${i + 1} name`} value={lv.name} disabled={readOnly} onChange={(e) => onChange({ ...c, levels: c.levels.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)) })} />
              </td>
              <td className="py-1 pr-2">
                <Input aria-label={`Level ${i + 1} marks`} inputMode="decimal" value={lv.marks} disabled={readOnly} onChange={(e) => onChange({ ...c, levels: c.levels.map((x, k) => (k === i ? { ...x, marks: e.target.value } : x)) })} />
              </td>
              <td className="py-1 pr-2">
                <Input aria-label={`Level ${i + 1} definition`} value={lv.definition} disabled={readOnly} onChange={(e) => onChange({ ...c, levels: c.levels.map((x, k) => (k === i ? { ...x, definition: e.target.value } : x)) })} />
              </td>
              <td>
                {!readOnly && c.levels.length > 2 ? (
                  <Button type="button" variant="ghost" size="sm" aria-label={`Remove level ${lv.name}`} onClick={() => onChange({ ...c, levels: c.levels.filter((_, k) => k !== i) })}>
                    <Trash2 aria-hidden="true" />
                  </Button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!readOnly ? (
        <Button
          type="button"
          variant="link"
          size="sm"
          onClick={() => {
            const last = c.levels.length - 1;
            const lvl = { id: newId("l"), name: "Partial", marks: "", definition: "" };
            onChange({ ...c, levels: [...c.levels.slice(0, last), lvl, ...c.levels.slice(last)] });
          }}
        >
          <Plus aria-hidden="true" /> Partial level
        </Button>
      ) : null}
      {mine.length ? (
        <ul className="text-sm text-destructive">
          {mine.map((i, k) => (
            <li key={k}>{i.message}</li>
          ))}
        </ul>
      ) : null}
    </fieldset>
  );
}

export function RubricEditor({ examId, canEdit }: { examId: string; canEdit: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [rubric, setRubric] = useState<Record<string, QuestionRubric>>({});
  const [policy, setPolicy] = useState<Policy>(DEFAULT_POLICY);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [message, setMessage] = useState<{ tone: "default" | "destructive"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = useCallback((s: State) => {
    setState(s);
    setIssues(s.issues);
    const cur = s.draft ?? s.approved;
    const byQ: Record<string, QuestionRubric> = {};
    for (const q of cur?.document.questions ?? []) byQ[q.qid] = q;
    if (s.paper) for (const lf of leaves(s.paper.document.questions)) byQ[lf.id] ??= { qid: lf.id, criteria: [defaultCriterion(lf.max_marks ?? "")] };
    setRubric(byQ);
    if (cur?.policy) setPolicy({ ...DEFAULT_POLICY, ...cur.policy });
  }, []);

  const load = useCallback(async () => {
    apply(await api<State>(`exams/${examId}/rubric`));
  }, [examId, apply]);

  // Initial fetch: state is set only from the promise callback (never synchronously in the effect body).
  useEffect(() => {
    let alive = true;
    api<State>(`exams/${examId}/rubric`)
      .then((s) => {
        if (alive) apply(s);
      })
      .catch(() => {
        if (alive) setMessage({ tone: "destructive", text: "We couldn't load the rubric. Please refresh the page." });
      });
    return () => {
      alive = false;
    };
  }, [examId, apply]);

  if (!state) return <p className="text-muted-foreground">Loading…</p>;
  if (!state.paper) return <Alert>Approve the question paper structure first; the rubric is written against it.</Alert>;
  const paper = state.paper.document;
  const labels = fullLabels(paper.questions);
  const leafNodes = leaves(paper.questions);

  async function run<T>(fn: () => Promise<T>, ok: string): Promise<T | undefined> {
    setBusy(true);
    setMessage(null);
    try {
      const r = await fn();
      setMessage({ tone: "default", text: ok });
      return r;
    } catch (e) {
      if (e instanceof ClientError) {
        setIssues(e.issues);
        setMessage({ tone: "destructive", text: e.message });
      } else setMessage({ tone: "destructive", text: "Something went wrong." });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const document: Rubric = { questions: leafNodes.map((lf) => rubric[lf.id]).filter((q): q is QuestionRubric => !!q) };
    const r = await run(() => api<State>(`exams/${examId}/rubric/draft`, { method: "PUT", json: { document, policy } }), "Draft saved.");
    if (r) {
      setState(r);
      setIssues(r.issues);
      if (r.issues.length) setMessage({ tone: "destructive", text: `Draft saved with ${r.issues.length} problem(s) to fix before approval.` });
    }
  }

  async function approve() {
    const r = await run(() => api<Version<Rubric>>(`exams/${examId}/rubric/approve`, { method: "POST" }), "Rubric approved. Booklets can now be graded.");
    if (r) await load();
  }

  const status = state.draft ? `Draft v${state.draft.version_no}` : state.approved ? `Approved v${state.approved.version_no}` : "Not started";
  const pol = (k: keyof Policy, v: string | boolean) => setPolicy((p) => ({ ...p, [k]: v }));
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={state.draft ? "warning" : state.approved ? "success" : "neutral"}>{status}</Badge>
        <span className="text-sm text-muted-foreground">Written against question paper v{state.paper.version_no}.</span>
        {state.stale_draft ? <Badge tone="danger">The paper changed since this draft was saved: save again</Badge> : null}
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Marking policy</CardTitle>
          <CardDescription>Stored with each approved rubric version, so every score can be reproduced.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-attempts">Several attempts of one question</Label>
            <Select id="p-attempts" value={policy.multiple_attempts} disabled={!canEdit} onChange={(e) => pol("multiple_attempts", e.target.value)}>
              <option value="last">Count the last attempt</option>
              <option value="first">Count the first attempt</option>
              <option value="best">Count the best attempt</option>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-or">Internal choice (OR)</Label>
            <Select id="p-or" value={policy.or_selection} disabled={!canEdit} onChange={(e) => pol("or_selection", e.target.value)}>
              <option value="best">Count the best alternatives</option>
              <option value="first_attempted">Count the first attempted</option>
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-round">Rounding</Label>
            <div className="flex gap-2">
              <Select id="p-round" value={policy.rounding_mode} disabled={!canEdit} onChange={(e) => pol("rounding_mode", e.target.value)}>
                <option value="none">None</option>
                <option value="half_up">Nearest</option>
                <option value="up">Up</option>
                <option value="down">Down</option>
              </Select>
              <Select aria-label="Rounding step" value={policy.rounding_step} disabled={!canEdit || policy.rounding_mode === "none"} onChange={(e) => pol("rounding_step", e.target.value)}>
                <option value="0.25">to 0.25</option>
                <option value="0.5">to 0.5</option>
                <option value="1">to 1</option>
              </Select>
              <Select aria-label="Rounding scope" value={policy.rounding_scope} disabled={!canEdit || policy.rounding_mode === "none"} onChange={(e) => pol("rounding_scope", e.target.value)}>
                <option value="total">on the total</option>
                <option value="question">per question</option>
              </Select>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={policy.negative_marking} disabled={!canEdit} onChange={(e) => pol("negative_marking", e.target.checked)} />
            Negative marking allowed
          </label>
        </CardContent>
      </Card>

      {leafNodes.map((lf) => {
        const q = rubric[lf.id];
        if (!q) return null;
        const qIssues = issues.filter((i) => i.path === `rubric/${lf.id}`);
        return (
          <Card key={lf.id}>
            <CardHeader>
              <CardTitle>
                Question {labels.get(lf.id)} <span className="font-normal text-muted-foreground">({lf.max_marks} marks)</span>
              </CardTitle>
              {lf.text ? <CardDescription>{lf.text}</CardDescription> : null}
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {q.criteria.map((c, ci) => (
                <CriterionEditor
                  key={c.id}
                  qid={lf.id}
                  c={c}
                  issues={issues}
                  readOnly={!canEdit}
                  onChange={(nc) =>
                    setRubric((r) => ({ ...r, [lf.id]: { ...q, criteria: nc ? q.criteria.map((x, k) => (k === ci ? nc : x)) : q.criteria.filter((_, k) => k !== ci) } }))
                  }
                />
              ))}
              {qIssues.length ? (
                <ul className="text-sm text-destructive">
                  {qIssues.map((i, k) => (
                    <li key={k}>{i.message}</li>
                  ))}
                </ul>
              ) : null}
              {canEdit ? (
                <div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setRubric((r) => ({ ...r, [lf.id]: { ...q, criteria: [...q.criteria, { ...defaultCriterion("1"), id: newId("c"), name: "" }] } }))}
                  >
                    <Plus aria-hidden="true" /> Criterion
                  </Button>
                </div>
              ) : null}
            </CardContent>
          </Card>
        );
      })}

      <IssueList issues={issues.filter((i) => i.path.startsWith("policy") || i.code === "missing_rubric")} title="Other problems" />
      {message ? (
        <Alert variant={message.tone} role={message.tone === "destructive" ? "alert" : "status"}>
          {message.text}
        </Alert>
      ) : null}
      {canEdit ? (
        <div className="flex gap-2">
          <Button type="button" onClick={() => void save()} disabled={busy}>
            Save draft
          </Button>
          <Button type="button" variant="outline" onClick={() => void approve()} disabled={busy || !state.draft || issues.length > 0 || state.stale_draft}>
            Approve rubric
          </Button>
        </div>
      ) : null}
    </div>
  );
}
