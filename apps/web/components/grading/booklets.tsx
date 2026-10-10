"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ClientError } from "@/lib/client";
import type { OcrSummary, SubmissionRow } from "@/lib/types";

const STATUS: Record<string, { text: string; tone: "neutral" | "success" | "warning" | "danger" }> = {
  QUEUED: { text: "Waiting to process", tone: "neutral" },
  RUNNING: { text: "Preparing pages…", tone: "warning" },
  COMPLETED: { text: "Ready", tone: "success" },
  REVIEW_REQUIRED: { text: "Ready", tone: "success" },
  FAILED: { text: "Failed", tone: "danger" },
};

/** What the examiner should read in the Status column. Grading never waits for machine reading (D28). */
function describe(r: SubmissionRow): { text: string; tone: "neutral" | "success" | "warning" | "danger"; note?: string } {
  if (!r.pages_ready) return STATUS[r.job_status ?? "QUEUED"] ?? { text: "Waiting to process", tone: "neutral" };
  if (r.job_status === "COMPLETED" || r.job_status === "REVIEW_REQUIRED") return { text: "Ready", tone: "success" };
  if (r.job_status === "FAILED") return { text: "Ready to grade", tone: "success", note: "Machine reading is unavailable for some pages." };
  return { text: "Ready to grade", tone: "success", note: "Machine reading is still running." };
}

export function Booklets({ examId, canUpload }: { examId: string; canUpload: boolean }) {
  const [rows, setRows] = useState<SubmissionRow[] | null>(null);
  const [ocr, setOcr] = useState<Record<string, OcrSummary>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const refresh = useCallback(async () => {
    setRows(await api<SubmissionRow[]>(`exams/${examId}/submissions?limit=100`));
    setOcr(Object.fromEntries((await api<OcrSummary[]>(`exams/${examId}/ocr-summary`)).map((o) => [o.submission_id, o])));
  }, [examId]);

  const pending = rows?.some((r) => r.job_status === "QUEUED" || r.job_status === "RUNNING") ?? false;

  useEffect(() => {
    let alive = true;
    const tick = () =>
      Promise.all([api<SubmissionRow[]>(`exams/${examId}/submissions?limit=100`), api<OcrSummary[]>(`exams/${examId}/ocr-summary`)])
        .then(([r, o]) => {
          if (!alive) return;
          setRows(r);
          setOcr(Object.fromEntries(o.map((x) => [x.submission_id, x])));
        })
        .catch(() => alive && setLoadError(true));
    void tick();
    // Poll while any booklet is still being turned into page images.
    const timer = pending ? setInterval(() => void tick(), 3000) : undefined;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [examId, pending]);

  async function upload(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    setBusy(true);
    setError(null);
    try {
      await api(`exams/${examId}/submissions`, { method: "POST", form: new FormData(form) });
      form.reset();
      await refresh();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Upload failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function rereadOcr(submissionId: string) {
    setError(null);
    try {
      await api(`submissions/${submissionId}/ocr/retry`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not start the machine reading again.");
    }
  }

  async function retry(jobId: string) {
    try {
      await api(`jobs/${jobId}/retry`, { method: "POST" });
      await refresh();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not retry.");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      {canUpload ? (
        <Card>
          <CardHeader>
            <CardTitle>Upload an answer booklet</CardTitle>
          </CardHeader>
          <CardContent>
            <form onSubmit={upload} className="grid gap-3 sm:grid-cols-4 sm:items-end" aria-label="Upload an answer booklet">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="student-ref">Student reference</Label>
                <Input id="student-ref" name="student_ref" required maxLength={64} pattern="[A-Za-z0-9][A-Za-z0-9._\-]{0,63}" placeholder="S-001 (not a name)" />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="booklet-file">Booklet (PDF, PNG or JPEG)</Label>
                <Input id="booklet-file" name="file" type="file" required accept="application/pdf,image/png,image/jpeg" />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="consent">Data use</Label>
                <Select id="consent" name="consent_scope" defaultValue="local_only">
                  <option value="local_only">Local only</option>
                  <option value="public_release">May be released publicly</option>
                </Select>
              </div>
              <Button type="submit" disabled={busy}>
                {busy ? "Uploading…" : "Upload"}
              </Button>
            </form>
          </CardContent>
        </Card>
      ) : null}
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      {loadError ? <Alert variant="destructive">We couldn&apos;t load the booklets. Please refresh the page.</Alert> : null}
      {rows === null ? (
        <p className="text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-muted-foreground">No booklets uploaded yet.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
            <table className="w-full text-sm">
              <caption className="sr-only">Answer booklets</caption>
              <thead className="border-b bg-muted text-left">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">Student</th>
                  <th scope="col" className="px-4 py-3 font-medium">Pages</th>
                  <th scope="col" className="px-4 py-3 font-medium">Status</th>
                  <th scope="col" className="px-4 py-3 font-medium">Machine reading</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const st = describe(r);
                  const ready = r.pages_ready;
                  const o = ocr[r.id];
                  return (
                    <tr key={r.id} className="border-b last:border-0">
                      <td className="px-4 py-3 font-medium">{r.student_ref}</td>
                      <td className="px-4 py-3">{r.page_count}</td>
                      <td className="px-4 py-3">
                        <Badge tone={st.tone}>{st.text}</Badge>
                        {st.note ? <span className="ml-2 text-muted-foreground">{st.note}</span> : null}
                        {r.job_status === "FAILED" && !r.pages_ready ? <span className="ml-2 text-muted-foreground">{r.job_error}</span> : null}
                      </td>
                      <td className="px-4 py-3 text-muted-foreground" data-testid="machine-reading">
                        {!o || o.pages === 0
                          ? "—"
                          : `${o.pages_read} of ${o.pages} pages read${o.pages_failed ? `, ${o.pages_failed} could not be read` : ""}`}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {ready ? (
                          <Button asChild size="sm">
                            <Link href={`/submissions/${r.id}`}>Grade</Link>
                          </Button>
                        ) : r.job_status === "FAILED" && r.job_id && canUpload ? (
                          <Button size="sm" variant="outline" onClick={() => void retry(r.job_id!)}>
                            Retry
                          </Button>
                        ) : null}
                        {ready && canUpload && r.job_status && r.job_status !== "QUEUED" && r.job_status !== "RUNNING" && o && o.pages_read < o.pages ? (
                          <Button size="sm" variant="ghost" className="ml-1" onClick={() => void rereadOcr(r.id)}>
                            Read text again
                          </Button>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
