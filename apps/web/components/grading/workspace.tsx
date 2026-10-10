"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, ClientError } from "@/lib/client";
import { fullLabels, leaves, type Criterion, type LineHighlight, type MachineReadingData, type WorkspaceData } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MachineReading } from "./machine-reading";
import { PageViewer } from "./page-viewer";

export function Workspace({ submissionId }: { submissionId: string }) {
  const [ws, setWs] = useState<WorkspaceData | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [qid, setQid] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(1);
  const [pageIdx, setPageIdx] = useState(0);
  const [activeRegion, setActiveRegion] = useState<string | null>(null);
  const [newAttempt, setNewAttempt] = useState(false);
  const [notice, setNotice] = useState<{ tone: "default" | "destructive"; text: string } | null>(null);
  const [help, setHelp] = useState(false);
  // Machine reading is display-only and optional: it loads separately, so its failure never affects grading
  const [mr, setMr] = useState<MachineReadingData | null>(null);
  const [mrFailed, setMrFailed] = useState(false);
  const [highlight, setHighlight] = useState<LineHighlight | null>(null);

  const loadMr = useCallback(
    () =>
      api<MachineReadingData>(`submissions/${submissionId}/machine-reading`).then(
        (d) => {
          setMr(d);
          setMrFailed(false);
        },
        () => setMrFailed(true),
      ),
    [submissionId],
  );

  const reload = useCallback(async () => {
    setWs(await api<WorkspaceData>(`submissions/${submissionId}/workspace`));
    void loadMr();
  }, [submissionId, loadMr]);

  useEffect(() => {
    let alive = true;
    api<WorkspaceData>(`submissions/${submissionId}/workspace`)
      .then((w) => alive && setWs(w))
      .catch((e) => alive && setFailed(e instanceof ClientError ? e.message : "We couldn't load this booklet. Please refresh the page."));
    void loadMr();
    return () => {
      alive = false;
    };
  }, [submissionId, loadMr]);

  const questions = useMemo(() => (ws ? leaves(ws.paper.questions) : []), [ws]);
  const labels = useMemo(() => (ws ? fullLabels(ws.paper.questions) : new Map<string, string>()), [ws]);

  const activeQ = qid ?? questions[0]?.id ?? null;
  const qIndex = questions.findIndex((q) => q.id === activeQ);

  const selectQuestion = useCallback(
    (id: string) => {
      setQid(id);
      setAttempt(1);
      setNewAttempt(false);
      const first = ws?.regions.find((r) => r.qid === id);
      if (first) {
        setActiveRegion(first.id);
        const i = ws?.pages.findIndex((p) => p.id === first.page_id) ?? -1;
        if (i >= 0) setPageIdx(i);
      } else {
        setActiveRegion(null);
      }
    },
    [ws],
  );

  // N / P / ? work on EVERY question, mapped or not (they used to live in the grading panel, which only exists once an
  // answer box does, so pressing N onto an unmapped question stranded a keyboard-only examiner)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === "TEXTAREA" || t.tagName === "SELECT" || (t.tagName === "INPUT" && (t as HTMLInputElement).type === "text") || t.isContentEditable);
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "?") setHelp((h) => !h);
      else if (e.key === "n" || e.key === "N" || e.key === "p" || e.key === "P") {
        const next = questions[qIndex + (e.key.toLowerCase() === "n" ? 1 : -1)];
        if (next) selectQuestion(next.id);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [questions, qIndex, selectQuestion]);

  if (failed) return <Alert variant="destructive">{failed}</Alert>;
  if (!ws) return <p className="text-muted-foreground">Loading…</p>;

  const qRegions = ws.regions.filter((r) => r.qid === activeQ);
  const attempts = [...new Set(qRegions.map((r) => r.attempt_no))].sort((a, b) => a - b);
  const curAttempt = attempts.includes(attempt) ? attempt : (attempts[attempts.length - 1] ?? 1);
  const criteria: Criterion[] = ws.rubric.questions.find((q) => q.qid === activeQ)?.criteria ?? [];
  const evaluation = ws.evaluations.find((e) => e.qid === activeQ && e.attempt_no === curAttempt) ?? null;

  async function run(fn: () => Promise<unknown>, ok?: string) {
    setNotice(null);
    try {
      await fn();
      await reload();
      if (ok) setNotice({ tone: "default", text: ok });
    } catch (err) {
      setNotice({ tone: "destructive", text: err instanceof ClientError ? err.message : "Something went wrong. Please try again." });
    }
  }

  const onDraw = (pageId: string, bbox: [number, number, number, number]) => {
    if (!activeQ) return;
    void run(
      () => api(`submissions/${submissionId}/regions`, { method: "POST", json: { page_id: pageId, bbox, qid: activeQ, new_attempt: newAttempt } }),
      "Answer area saved.",
    );
    setNewAttempt(false);
  };

  const nodeMarks = (id: string) => ws.score.nodes[id] as { marks?: string; max_marks?: string; status?: string } | undefined;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link href={`/exams/${ws.exam_id}/submissions`} className="text-sm text-muted-foreground underline-offset-4 hover:underline">
            ← Booklets
          </Link>
          <h1 className="text-2xl font-semibold">{ws.student_ref}</h1>
        </div>
        <div className="flex items-center gap-2" aria-live="polite">
          <span className="text-lg font-semibold tabular-nums" data-testid="total">
            {ws.score.total} / {ws.score.max_total}
          </span>
          <Badge tone={ws.score.complete ? "success" : "warning"}>{ws.score.complete ? "Complete" : "In progress"}</Badge>
          {ws.score.flags.map((f) => (
            <Badge key={f} tone="warning">
              {f.replaceAll("_", " ").toLowerCase()}
            </Badge>
          ))}
          <Button size="sm" variant="outline" onClick={() => setHelp((h) => !h)} aria-expanded={help}>
            Shortcuts (?)
          </Button>
        </div>
      </div>
      {help ? (
        <Alert>
          <strong>Keyboard:</strong> 1–9 pick a level for the highlighted criterion and move to the next · N / P next / previous question · Ctrl+Enter save · ? show or hide this help. Shortcuts are off while you type in a box.
        </Alert>
      ) : null}
      {notice ? <Alert variant={notice.tone}>{notice.text}</Alert> : null}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <PageViewer
          pages={ws.pages}
          pageIdx={pageIdx}
          onPage={setPageIdx}
          regions={ws.regions}
          labels={labels}
          activeRegionId={activeRegion}
          highlight={highlight}
          canDraw={activeQ !== null}
          drawHint={`Drag on the page to mark the answer to ${activeQ ? (labels.get(activeQ) ?? activeQ) : "a question"}${newAttempt ? " (new attempt)" : ""}.`}
          onDraw={onDraw}
          onSelectRegion={(id) => {
            const r = ws.regions.find((x) => x.id === id);
            if (!r) return;
            setActiveRegion(id);
            setQid(r.qid);
            setAttempt(r.attempt_no);
          }}
        />
        <div className="flex flex-col gap-4">
          <Card className="gap-3 py-4">
            <CardHeader className="px-4">
              <CardTitle className="text-base">Questions</CardTitle>
            </CardHeader>
            <CardContent className="px-4">
              <ul className="flex max-h-56 flex-col gap-1 overflow-y-auto" aria-label="Questions">
                {questions.map((q) => {
                  const mapped = ws.regions.some((r) => r.qid === q.id);
                  const m = nodeMarks(q.id);
                  return (
                    <li key={q.id}>
                      <button
                        type="button"
                        onClick={() => selectQuestion(q.id)}
                        aria-current={q.id === activeQ ? "true" : undefined}
                        className={cn("flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent", q.id === activeQ && "bg-accent font-medium")}
                      >
                        <span>{labels.get(q.id) ?? q.label}</span>
                        <span className="text-muted-foreground tabular-nums">
                          {mapped ? `${m?.marks ?? "0"} / ${m?.max_marks ?? q.max_marks ?? "?"}` : "not mapped"}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </CardContent>
          </Card>
          {activeQ ? (
            <MachineReading
              data={mr}
              failed={mrFailed}
              qid={activeQ}
              attempt={curAttempt}
              pageNo={(id) => ws.pages.find((p) => p.id === id)?.page_no}
              onHighlight={(h) => {
                setHighlight(h);
                const i = h ? ws.pages.findIndex((p) => p.id === h.page_id) : -1;
                if (i >= 0) setPageIdx(i);
              }}
            />
          ) : null}
          {activeQ ? (
            <Card className="gap-3 py-4">
              <CardHeader className="px-4">
                <CardTitle className="text-base">
                  {labels.get(activeQ) ?? activeQ}
                  {attempts.length > 1 ? ` · attempt ${curAttempt}` : ""}
                </CardTitle>
                <p className="whitespace-pre-wrap text-sm text-muted-foreground">{questions.find((q) => q.id === activeQ)?.text}</p>
              </CardHeader>
              <CardContent className="flex flex-col gap-4 px-4">
                <section aria-label="Answer areas" className="flex flex-col gap-2">
                  <h3 className="text-sm font-medium">Answer areas</h3>
                  {qRegions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Choose “Draw answer box” on the page, then drag over this answer.</p>
                  ) : (
                    <ul className="flex flex-col gap-1">
                      {qRegions.map((r) => (
                        <li key={r.id} className="flex flex-wrap items-center gap-2 text-sm">
                          <button
                            type="button"
                            className="underline-offset-4 hover:underline"
                            onClick={() => {
                              setActiveRegion(r.id);
                              setAttempt(r.attempt_no);
                              const i = ws.pages.findIndex((p) => p.id === r.page_id);
                              if (i >= 0) setPageIdx(i);
                            }}
                          >
                            Page {ws.pages.find((p) => p.id === r.page_id)?.page_no ?? "?"}
                            {attempts.length > 1 ? ` · attempt ${r.attempt_no}` : ""}
                          </button>
                          <label className="flex items-center gap-1">
                            <input
                              type="checkbox"
                              checked={r.crossed_out}
                              onChange={(e) => void run(() => api(`submissions/${submissionId}/regions/${r.id}/crossed`, { method: "POST", json: { crossed_out: e.target.checked } }))}
                            />
                            crossed out
                          </label>
                          <Button size="sm" variant="ghost" onClick={() => void run(() => api(`submissions/${submissionId}/regions/${r.id}`, { method: "DELETE" }), "Answer area removed.")}>
                            Remove
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={newAttempt} onChange={(e) => setNewAttempt(e.target.checked)} />
                    Next box starts a new attempt (the student answered this question again)
                  </label>
                </section>
                {attempts.length > 1 ? (
                  <div className="flex gap-1" role="group" aria-label="Attempts">
                    {attempts.map((a) => (
                      <Button key={a} size="sm" variant={a === curAttempt ? "default" : "outline"} onClick={() => setAttempt(a)}>
                        Attempt {a}
                      </Button>
                    ))}
                  </div>
                ) : null}
                {qRegions.length > 0 ? (
                  <GradePanel
                    key={`${activeQ}:${curAttempt}:${evaluation?.id ?? "none"}`}
                    criteria={criteria}
                    initialVerdicts={evaluation?.verdicts ?? {}}
                    initialNotes={evaluation?.notes ?? ""}
                    marks={evaluation?.marks ?? null}
                    max={nodeMarks(activeQ)?.max_marks ?? null}
                    onSave={async (verdicts, notes, overrideReason) => {
                      await api(`submissions/${submissionId}/evaluations/${encodeURIComponent(activeQ)}/${curAttempt}`, {
                        method: "PUT",
                        json: { verdicts, notes, override_reason: overrideReason || null },
                      });
                      await reload();
                      setNotice({ tone: "default", text: "Grade saved." });
                    }}
                  />
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function GradePanel({
  criteria,
  initialVerdicts,
  initialNotes,
  marks,
  max,
  onSave,
}: {
  criteria: Criterion[];
  initialVerdicts: Record<string, string>;
  initialNotes: string;
  marks: string | null;
  max: string | null;
  onSave: (verdicts: Record<string, string>, notes: string, overrideReason: string) => Promise<void>;
}) {
  const [verdicts, setVerdicts] = useState(initialVerdicts);
  const [notes, setNotes] = useState(initialNotes);
  const [focus, setFocus] = useState(0);
  const [reason, setReason] = useState("");
  const [needReason, setNeedReason] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      await onSave(verdicts, notes, reason);
    } catch (err) {
      if (err instanceof ClientError && err.code === "override_reason_required") setNeedReason(true);
      setError(err instanceof ClientError ? err.message : "Could not save. Please try again.");
    } finally {
      setBusy(false);
    }
  }, [onSave, verdicts, notes, reason]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement | null;
      const typing = !!t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && (t as HTMLInputElement).type === "text") || t.isContentEditable);
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void save();
        return;
      }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^[1-9]$/.test(e.key)) {
        const c = criteria[focus];
        const level = c?.levels[Number(e.key) - 1];
        if (c && level) {
          setVerdicts((v) => ({ ...v, [c.id]: level.id }));
          setFocus((f) => Math.min(criteria.length - 1, f + 1));
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [criteria, focus, save]);

  if (criteria.length === 0) return <Alert>This question has no rubric criteria, so there is nothing to grade.</Alert>;

  return (
    <section aria-label="Grade this answer" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Grade</h3>
        <span className="text-sm tabular-nums" data-testid="question-marks">
          {marks ?? "–"} / {max ?? "?"}
        </span>
      </div>
      {criteria.map((c, ci) => (
        <fieldset key={c.id} className={cn("rounded-md border p-3", ci === focus && "ring-2 ring-primary/40")} onFocus={() => setFocus(ci)} onClick={() => setFocus(ci)}>
          <legend className="px-1 text-sm font-medium">{c.name}</legend>
          {c.description ? <p className="mb-2 text-xs text-muted-foreground">{c.description}</p> : null}
          <div className="flex flex-col gap-1">
            {c.levels.map((lv, li) => (
              <label key={lv.id} className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 text-sm hover:bg-accent">
                <input type="radio" name={`c-${c.id}`} checked={verdicts[c.id] === lv.id} onChange={() => setVerdicts((v) => ({ ...v, [c.id]: lv.id }))} className="mt-1" />
                <span>
                  <kbd className="mr-1 rounded border px-1 text-xs">{li + 1}</kbd>
                  {lv.name} <span className="text-muted-foreground">({lv.marks})</span>
                  {lv.definition ? <span className="block text-xs text-muted-foreground">{lv.definition}</span> : null}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
      ))}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="eval-notes">Notes (optional)</Label>
        <Textarea id="eval-notes" value={notes} maxLength={4000} onChange={(e) => setNotes(e.target.value)} rows={2} />
      </div>
      {needReason ? (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="override-reason">Reason for changing another examiner&apos;s grade</Label>
          <Textarea id="override-reason" value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} rows={2} />
        </div>
      ) : null}
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      <Button onClick={() => void save()} disabled={busy}>
        {busy ? "Saving…" : "Save grade"}
      </Button>
    </section>
  );
}
