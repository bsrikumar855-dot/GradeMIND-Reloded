"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/client";
import type { TotalsData } from "@/lib/types";

export function Totals({ examId, canReport }: { examId: string; canReport: boolean }) {
  const [data, setData] = useState<TotalsData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api<TotalsData>(`exams/${examId}/totals`)
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [examId]);

  if (failed) return <Alert variant="destructive">We couldn&apos;t load the totals. Please refresh the page.</Alert>;
  if (!data) return <p className="text-muted-foreground">Loading…</p>;
  if (data.columns.length === 0) return <p className="text-muted-foreground">Totals appear once the rubric is approved and booklets are graded.</p>;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap justify-end gap-2">
        <Button asChild variant="outline">
          <a href={`/api/proxy/exams/${examId}/totals.csv`} download>
            Download CSV
          </a>
        </Button>
        {canReport ? (
          <>
            <Button asChild variant="outline">
              <a href={`/api/proxy/exams/${examId}/summary.csv`} download data-testid="summary-csv">
                Summary of finalized results (CSV)
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href={`/api/proxy/exams/${examId}/summary.pdf`} download data-testid="summary-pdf">
                Summary (PDF)
              </a>
            </Button>
          </>
        ) : null}
      </div>
      <Card className="py-0">
        <CardContent className="overflow-x-auto px-0">
          <table className="w-full text-sm">
            <caption className="sr-only">Totals per student</caption>
            <thead className="border-b bg-muted text-left">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">Student</th>
                {data.columns.map((c) => (
                  <th key={c.id} scope="col" className="px-4 py-3 text-right font-medium">
                    {c.label} <span className="text-muted-foreground">/{c.max}</span>
                  </th>
                ))}
                <th scope="col" className="px-4 py-3 text-right font-medium">Total</th>
                <th scope="col" className="px-4 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.submission_id} className="border-b last:border-0">
                  <td className="px-4 py-3 font-medium">
                    <Link className="underline-offset-4 hover:underline" href={`/submissions/${r.submission_id}`}>
                      {r.student_ref}
                    </Link>
                  </td>
                  {data.columns.map((c) => (
                    <td key={c.id} className="px-4 py-3 text-right tabular-nums">
                      {r.sections[c.id] ?? "–"}
                    </td>
                  ))}
                  <td className="px-4 py-3 text-right font-semibold tabular-nums">
                    {r.total ?? "–"} <span className="font-normal text-muted-foreground">/{r.max_total}</span>
                  </td>
                  <td className="px-4 py-3">
                    {r.total === null ? <Badge>Not started</Badge> : r.complete ? <Badge tone="success">Complete</Badge> : <Badge tone="warning">Incomplete</Badge>}
                    {r.finalized ? <Badge tone="success">Finalized (snapshot {r.snapshot_no})</Badge> : null}
                    {r.flags.map((f) => (
                      <Badge key={f} tone="warning" className="ml-1">
                        {f.replaceAll("_", " ").toLowerCase()}
                      </Badge>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
