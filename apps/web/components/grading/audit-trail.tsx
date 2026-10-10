"use client";

import { useEffect, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/client";
import type { AuditRow } from "@/lib/types";

export function AuditTrail({ examId }: { examId: string }) {
  const [rows, setRows] = useState<AuditRow[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    api<AuditRow[]>(`exams/${examId}/audit?limit=500`)
      .then((d) => alive && setRows(d))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [examId]);

  if (failed) return <Alert variant="destructive">We couldn&apos;t load the audit trail. Please refresh the page.</Alert>;
  if (!rows) return <p className="text-muted-foreground">Loading…</p>;
  if (rows.length === 0) return <p className="text-muted-foreground">Nothing recorded yet.</p>;
  return (
    <Card className="py-0">
      <CardContent className="overflow-x-auto px-0">
        <table className="w-full text-sm">
          <caption className="sr-only">Audit trail, oldest first</caption>
          <thead className="border-b bg-muted text-left">
            <tr>
              <th scope="col" className="px-4 py-3 font-medium">When</th>
              <th scope="col" className="px-4 py-3 font-medium">Action</th>
              <th scope="col" className="px-4 py-3 font-medium">Details</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const rest = Object.fromEntries(Object.entries(r.details).filter(([k]) => k !== "exam_id" && k !== "submission_id"));
              return (
                <tr key={`${r.at}-${i}`} className="border-b align-top last:border-0">
                  <td className="whitespace-nowrap px-4 py-3 tabular-nums">{new Date(r.at).toLocaleString()}</td>
                  <td className="px-4 py-3 font-medium">{r.action}</td>
                  <td className="px-4 py-3 text-muted-foreground">
                    {Object.entries(rest)
                      .map(([k, v]) => `${k}: ${String(v)}`)
                      .join(" · ")}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  );
}
