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
import type { SubmissionRow } from "@/lib/types";

const STATUS: Record<string, { text: string; tone: "neutral" | "success" | "warning" | "danger" }> = {
  QUEUED: { text: "Waiting to process", tone: "neutral" },
  RUNNING: { text: "Preparing pages…", tone: "warning" },
  COMPLETED: { text: "Ready", tone: "success" },
  REVIEW_REQUIRED: { text: "Ready", tone: "success" },
  FAILED: { text: "Failed", tone: "danger" },
};

export function Booklets({ examId, canUpload }: { examId: string; canUpload: boolean }) {
  const [rows, setRows] = useState<SubmissionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);

  const refresh = useCallback(async () => {
    setRows(await api<SubmissionRow[]>(`exams/${examId}/submissions?limit=100`));
  }, [examId]);

  const pending = rows?.some((r) => r.job_status === "QUEUED" || r.job_status === "RUNNING") ?? false;

  useEffect(() => {
    let alive = true;
    const tick = () =>
      api<SubmissionRow[]>(`exams/${examId}/submissions?limit=100`)
        .then((r) => alive && setRows(r))
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
                  <th scope="col" className="px-4 py-3 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const st = STATUS[r.job_status ?? "QUEUED"] ?? { text: "Waiting to process", tone: "neutral" as const };
                  const ready = r.page_count > 0 && (r.job_status === "COMPLETED" || r.job_status === "REVIEW_REQUIRED");
                  return (
                    <tr key={r.id} className="border-b last:border-0">
                      <td className="px-4 py-3 font-medium">{r.student_ref}</td>
                      <td className="px-4 py-3">{r.page_count}</td>
                      <td className="px-4 py-3">
                        <Badge tone={st.tone}>{st.text}</Badge>
                        {r.job_status === "FAILED" ? <span className="ml-2 text-muted-foreground">{r.job_error}</span> : null}
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
