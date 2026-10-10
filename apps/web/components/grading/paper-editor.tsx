"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { IssueList } from "@/components/grading/issues";
import { api, ClientError, type Issue } from "@/lib/client";
import type { Paper, PaperNode, Version } from "@/lib/types";

type State = { draft: Version<Paper> | null; approved: Version<Paper> | null; issues: Issue[] };

/** Internal ids only need to be unique and stable; examiners see labels. */
function newId(): string {
  return "n" + Math.random().toString(36).slice(2, 9);
}

function uniqueId(base: string, taken: Set<string>): string {
  let id = base;
  for (let i = 2; taken.has(id); i++) id = `${base}-${i}`;
  return id;
}

function allIds(nodes: PaperNode[]): Set<string> {
  const s = new Set<string>();
  const walk = (ns: PaperNode[]) => ns.forEach((n) => (s.add(n.id), walk(n.children ?? [])));
  walk(nodes);
  return s;
}

function updateAt(nodes: PaperNode[], path: number[], fn: (n: PaperNode) => PaperNode | null): PaperNode[] {
  const [i, ...rest] = path;
  return nodes.flatMap((n, k) => {
    if (k !== i) return [n];
    if (rest.length === 0) {
      const r = fn(n);
      return r ? [r] : [];
    }
    return [{ ...n, children: updateAt(n.children ?? [], rest, fn) }];
  });
}

function NodeEditor({
  node,
  path,
  depth,
  issues,
  readOnly,
  onChange,
}: {
  node: PaperNode;
  path: number[];
  depth: number;
  issues: Issue[];
  readOnly: boolean;
  onChange: (path: number[], fn: (n: PaperNode) => PaperNode | null) => void;
}) {
  const kids = node.children ?? [];
  const isLeaf = kids.length === 0;
  const mine = issues.filter((i) => i.path.split("/").at(-1) === node.id);
  const base = `n-${node.id}`;
  return (
    <li className={depth > 0 ? "border-l pl-4" : ""}>
      <div className={`flex flex-wrap items-end gap-2 rounded-md p-2 ${mine.length ? "bg-destructive/5 ring-1 ring-destructive/40" : ""}`}>
        <div className="flex w-24 flex-col gap-1">
          <Label htmlFor={`${base}-label`} className="text-xs">
            Label
          </Label>
          <Input id={`${base}-label`} value={node.label} disabled={readOnly} onChange={(e) => onChange(path, (n) => ({ ...n, label: e.target.value }))} />
        </div>
        <div className="flex min-w-48 flex-1 flex-col gap-1">
          <Label htmlFor={`${base}-text`} className="text-xs">
            Question text
          </Label>
          <Input id={`${base}-text`} value={node.text ?? ""} disabled={readOnly} onChange={(e) => onChange(path, (n) => ({ ...n, text: e.target.value }))} />
        </div>
        {isLeaf ? (
          <div className="flex w-24 flex-col gap-1">
            <Label htmlFor={`${base}-marks`} className="text-xs">
              Marks
            </Label>
            <Input
              id={`${base}-marks`}
              inputMode="decimal"
              value={node.max_marks ?? ""}
              disabled={readOnly}
              onChange={(e) => onChange(path, (n) => ({ ...n, max_marks: e.target.value || null }))}
            />
          </div>
        ) : (
          <div className="flex w-32 flex-col gap-1">
            <Label htmlFor={`${base}-choose`} className="text-xs">
              Answer any (OR)
            </Label>
            <Input
              id={`${base}-choose`}
              inputMode="numeric"
              placeholder="all"
              value={node.choose ?? ""}
              disabled={readOnly}
              onChange={(e) => onChange(path, (n) => ({ ...n, choose: e.target.value ? Number(e.target.value) : null }))}
            />
          </div>
        )}
        {!readOnly ? (
          <div className="flex gap-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              aria-label={`Add a sub-question to ${node.label}`}
              onClick={() =>
                onChange(path, (n) => {
                  const letter = String.fromCharCode(97 + (n.children?.length ?? 0));
                  const kid: PaperNode = { id: newId(), label: `(${letter})`, text: "", max_marks: null };
                  return { ...n, max_marks: null, choose: n.choose ?? null, children: [...(n.children ?? []), kid] };
                })
              }
            >
              <Plus aria-hidden="true" /> Sub-question
            </Button>
            <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${node.label}`} onClick={() => onChange(path, () => null)}>
              <Trash2 aria-hidden="true" />
            </Button>
          </div>
        ) : null}
      </div>
      {mine.length ? (
        <ul className="ml-2 text-sm text-destructive">
          {mine.map((i, k) => (
            <li key={k}>{i.message}</li>
          ))}
        </ul>
      ) : null}
      {kids.length ? (
        <ul className="mt-1 flex flex-col gap-1">
          {kids.map((k, i) => (
            <NodeEditor key={k.id} node={k} path={[...path, i]} depth={depth + 1} issues={issues} readOnly={readOnly} onChange={onChange} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function PaperEditor({ examId, examTotal, canEdit }: { examId: string; examTotal: string; canEdit: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [doc, setDoc] = useState<Paper>({ total_marks: examTotal, questions: [] });
  const [text, setText] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [message, setMessage] = useState<{ tone: "default" | "destructive"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const s = await api<State>(`exams/${examId}/paper`);
    setState(s);
    setIssues(s.issues);
    const current = s.draft ?? s.approved;
    if (current) setDoc(current.document);
  }, [examId]);

  useEffect(() => {
    void load();
  }, [load]);

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

  async function uploadSource(file: File) {
    const form = new FormData();
    form.set("file", file);
    const r = await run(() => api<{ text: string; has_text_layer: boolean }>(`exams/${examId}/paper/source`, { method: "POST", form }), "Paper uploaded.");
    if (r) {
      if (r.has_text_layer) setText(r.text);
      else setMessage({ tone: "default", text: "This file has no text layer (a scan). Type or paste the questions, or build the structure by hand." });
    }
  }

  async function parse() {
    const r = await run(() => api<{ draft: Paper; warnings: string[] }>("paper/parse", { method: "POST", json: { text } }), "Parsed. Check the structure below, then save.");
    if (r) {
      setDoc({ ...r.draft, total_marks: r.draft.total_marks === "0" ? examTotal : r.draft.total_marks });
      setWarnings(r.warnings);
    }
  }

  async function save() {
    const r = await run(() => api<State>(`exams/${examId}/paper/draft`, { method: "PUT", json: { document: doc } }), "Draft saved.");
    if (r) {
      setState(r);
      setIssues(r.issues);
      if (r.issues.length) setMessage({ tone: "destructive", text: `Draft saved with ${r.issues.length} problem(s) to fix before approval.` });
    }
  }

  async function approve() {
    const r = await run(() => api<Version<Paper>>(`exams/${examId}/paper/approve`, { method: "POST" }), "Structure approved. You can now write the rubric.");
    if (r) await load();
  }

  const change = (path: number[], fn: (n: PaperNode) => PaperNode | null) => setDoc((d) => ({ ...d, questions: updateAt(d.questions, path, fn) }));

  if (!state) return <p className="text-muted-foreground">Loading…</p>;
  const status = state.draft ? `Draft v${state.draft.version_no}` : state.approved ? `Approved v${state.approved.version_no}` : "Not started";
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2">
        <Badge tone={state.draft ? "warning" : state.approved ? "success" : "neutral"}>{status}</Badge>
        {state.approved && state.draft ? <span className="text-sm text-muted-foreground">v{state.approved.version_no} stays in use until this draft is approved.</span> : null}
      </div>
      {canEdit ? (
        <Card>
          <CardHeader>
            <CardTitle>Start from the paper</CardTitle>
            <CardDescription>Upload a PDF with selectable text, or paste the questions. Parsing is rule-based (no AI): check the result.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="paper-file">Question paper file (PDF or image)</Label>
              <Input id="paper-file" type="file" accept="application/pdf,image/png,image/jpeg" onChange={(e) => e.target.files?.[0] && void uploadSource(e.target.files[0])} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="paper-text">Question paper text</Label>
              <Textarea id="paper-text" rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={"1. Define ecosystem. [2]\n2. (a) Explain the water cycle. [3]\nOR\n(b) Explain the carbon cycle. [3]"} />
            </div>
            <div>
              <Button type="button" onClick={() => void parse()} disabled={busy || !text.trim()}>
                Parse into questions
              </Button>
            </div>
            {warnings.length ? (
              <Alert>
                <p className="mb-1 font-medium">Lines the parser could not place</p>
                <ul className="list-disc pl-5">
                  {warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </Alert>
            ) : null}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle>Structure</CardTitle>
          <CardDescription>Questions, sub-questions, marks and internal choices (“answer any N”).</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex w-40 flex-col gap-1.5">
            <Label htmlFor="paper-total">Paper total</Label>
            <Input id="paper-total" inputMode="decimal" value={doc.total_marks} disabled={!canEdit} onChange={(e) => setDoc((d) => ({ ...d, total_marks: e.target.value }))} />
          </div>
          {doc.questions.length ? (
            <ul className="flex flex-col gap-2" aria-label="Questions">
              {doc.questions.map((q, i) => (
                <NodeEditor key={q.id} node={q} path={[i]} depth={0} issues={issues} readOnly={!canEdit} onChange={change} />
              ))}
            </ul>
          ) : (
            <p className="text-muted-foreground">No questions yet.</p>
          )}
          {canEdit ? (
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={() =>
                  setDoc((d) => {
                    const n = d.questions.length + 1;
                    return { ...d, questions: [...d.questions, { id: uniqueId(`q${n}`, allIds(d.questions)), label: String(n), text: "", max_marks: null }] };
                  })
                }
              >
                <Plus aria-hidden="true" /> Add question
              </Button>
            </div>
          ) : null}
          <IssueList issues={issues.filter((i) => !allIds(doc.questions).has(i.path.split("/").at(-1) ?? ""))} title="Problems with the paper as a whole" />
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
              <Button type="button" variant="outline" onClick={() => void approve()} disabled={busy || !state.draft || issues.length > 0}>
                Approve structure
              </Button>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

