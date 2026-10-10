"use client";

import { useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ClientError } from "@/lib/client";
import type { UserRow } from "@/lib/types";

/** Who may grade this exam. An examiner sees only the exams they are assigned to; an administrator manages the list. */
function candidatesOf(all: UserRow[], assigned: UserRow[]): UserRow[] {
  const taken = new Set(assigned.map((u) => u.id));
  return all.filter((u) => u.role === "examiner" && u.is_active && !taken.has(u.id));
}

export function ExamExaminers({ examId, initialAssigned, initialUsers }: { examId: string; initialAssigned: UserRow[]; initialUsers: UserRow[] }) {
  const [assigned, setAssigned] = useState<UserRow[]>(initialAssigned);
  const [candidates, setCandidates] = useState<UserRow[]>(candidatesOf(initialUsers, initialAssigned));
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const [a, all] = await Promise.all([api<UserRow[]>(`exams/${examId}/assignments`), api<UserRow[]>("users")]);
      setAssigned(a);
      setCandidates(candidatesOf(all, a));
    } catch {
      setError("We couldn't refresh the list. Please reload the page.");
    }
  }

  async function assign(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const userId = new FormData(e.currentTarget).get("user_id");
    if (!userId) return;
    setError(null);
    try {
      await api(`exams/${examId}/assignments`, { method: "POST", json: { user_id: userId } });
      await load();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not assign the examiner.");
    }
  }

  async function remove(u: UserRow) {
    setError(null);
    try {
      await api(`exams/${examId}/assignments/${u.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not remove the examiner.");
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Assign an examiner</CardTitle>
        </CardHeader>
        <CardContent>
          {candidates.length === 0 ? (
            <p className="text-muted-foreground">No other active examiners to assign. Create examiner accounts under Users.</p>
          ) : (
            <form onSubmit={assign} className="flex flex-wrap items-end gap-3" aria-label="Assign an examiner">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="assign-user">Examiner</Label>
                <Select id="assign-user" name="user_id" defaultValue={candidates[0]?.id}>
                  {candidates.map((u) => (
                    <option key={u.id} value={u.id}>
                      {u.display_name} ({u.email})
                    </option>
                  ))}
                </Select>
              </div>
              <Button type="submit">Assign</Button>
            </form>
          )}
        </CardContent>
      </Card>
      {error ? <Alert variant="destructive">{error}</Alert> : null}
      {assigned.length === 0 ? (
        <p className="text-muted-foreground">No examiner is assigned yet. Until one is, only administrators and teachers can open this exam.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="overflow-x-auto px-0">
            <table className="w-full text-sm">
              <caption className="sr-only">Assigned examiners</caption>
              <thead className="border-b bg-muted text-left">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">Name</th>
                  <th scope="col" className="px-4 py-3 font-medium">Email</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {assigned.map((u) => (
                  <tr key={u.id} className="border-b last:border-0" data-testid="assigned-row">
                    <td className="px-4 py-3 font-medium">{u.display_name}</td>
                    <td className="px-4 py-3">{u.email}</td>
                    <td className="px-4 py-3 text-right">
                      <Button size="sm" variant="outline" onClick={() => void remove(u)} aria-label={`Remove ${u.display_name}`}>
                        Remove
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
