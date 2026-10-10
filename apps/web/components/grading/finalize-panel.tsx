"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api, ClientError } from "@/lib/client";
import type { FinalizationData } from "@/lib/types";

const MIN_REASON = 10;
const when = (iso: string) => new Date(iso).toLocaleString();

/**
 * Sign-off. Finalizing freezes this booklet's result (read-only, an append-only snapshot); reopening needs a reason and is recorded.
 * Only administrators and teachers can do either (`canManage`); an examiner sees the state.
 */
export function FinalizePanel({
  submissionId,
  data,
  canManage,
  onChanged,
}: {
  submissionId: string;
  data: FinalizationData | null;
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const [confirmNA, setConfirmNA] = useState(false);
  const [reason, setReason] = useState("");
  const [reopening, setReopening] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!data) return null;

  async function act(path: string, json: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api(`submissions/${submissionId}/${path}`, { method: "POST", json });
      setReopening(false);
      setReason("");
      setConfirmNA(false);
      await onChanged();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "That did not work. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (data.state === "FINALIZED" && data.snapshot) {
    const s = data.snapshot;
    return (
      <Card className="gap-3 py-4" data-testid="finalize-panel" data-state="FINALIZED">
        <CardHeader className="px-4">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            Result finalized <Badge tone="success">Snapshot {s.snapshot_no}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 px-4 text-sm">
          <p>
            <strong className="tabular-nums" data-testid="final-total">
              {s.total} / {s.max_total}
            </strong>{" "}
            frozen by {s.finalized_by} on {when(s.finalized_at)} (rubric version {s.rubric_version_no}, {s.score_computer_version}). Grades are read-only until it is reopened.
          </p>
          {canManage ? (
            <div>
              <Button asChild variant="outline" size="sm">
                <a href={`/api/proxy/submissions/${submissionId}/report.pdf`} download data-testid="report-pdf">
                  Download result sheet (PDF)
                </a>
              </Button>
            </div>
          ) : null}
          {canManage ? (
            reopening ? (
              <div className="flex flex-col gap-2">
                <Label htmlFor="reopen-reason">Why is it being reopened? (at least {MIN_REASON} characters; it is kept in the record)</Label>
                <Textarea id="reopen-reason" value={reason} onChange={(e) => setReason(e.target.value)} rows={2} maxLength={1000} />
                <div className="flex gap-2">
                  <Button size="sm" disabled={busy || reason.trim().length < MIN_REASON} onClick={() => void act("reopen", { reason })}>
                    Reopen for changes
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => setReopening(false)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div>
                <Button variant="outline" size="sm" onClick={() => setReopening(true)}>
                  Reopen…
                </Button>
              </div>
            )
          ) : null}
          {error ? <Alert variant="destructive">{error}</Alert> : null}
          <History data={data} />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="gap-3 py-4" data-testid="finalize-panel" data-state="OPEN">
      <CardHeader className="px-4">
        <CardTitle className="text-base">Sign-off</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4 text-sm">
        {data.blockers.length > 0 ? (
          <ul className="list-disc pl-5 text-muted-foreground" aria-label="Before this can be finalized">
            {data.blockers.map((b) => (
              <li key={b.code + b.message}>{b.message}</li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">Every answer that was mapped has a verdict for every criterion.</p>
        )}
        {data.ready && data.not_attempted.length > 0 ? (
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={confirmNA} onChange={(e) => setConfirmNA(e.target.checked)} className="mt-1" />
            <span>
              No answer box for {data.not_attempted.map((n) => n.label).join(", ")}: these count as not attempted (0 marks). I have checked the booklet.
            </span>
          </label>
        ) : null}
        {canManage ? (
          <div>
            <Button
              size="sm"
              disabled={busy || !data.ready || (data.not_attempted.length > 0 && !confirmNA)}
              onClick={() => void act("finalize", { confirm_not_attempted: confirmNA })}
            >
              Finalize result
            </Button>
          </div>
        ) : (
          <p className="text-muted-foreground">An administrator or teacher finalizes the result once it is complete.</p>
        )}
        {error ? <Alert variant="destructive">{error}</Alert> : null}
        <History data={data} />
      </CardContent>
    </Card>
  );
}

function History({ data }: { data: FinalizationData }) {
  if (data.history.length === 0) return null;
  return (
    <details>
      <summary className="cursor-pointer text-muted-foreground">History ({data.history.length})</summary>
      <ol className="mt-2 flex flex-col gap-1" aria-label="Finalize and reopen history">
        {data.history.map((h, i) => (
          <li key={i} data-testid="finalize-history">
            {h.action === "FINALIZED" ? "Finalized" : "Reopened"} · snapshot {h.snapshot_no} · {h.by} · {when(h.at)}
            {h.reason ? <span className="block text-muted-foreground">Reason: {h.reason}</span> : null}
          </li>
        ))}
      </ol>
    </details>
  );
}
