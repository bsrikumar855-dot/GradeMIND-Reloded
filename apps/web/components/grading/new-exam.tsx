"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert } from "@/components/ui/alert";
import { api, ClientError } from "@/lib/client";

export function NewExamForm() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true);
    setError(null);
    try {
      const exam = await api<{ id: string }>("exams", {
        method: "POST",
        json: { name: f.get("name"), subject: f.get("subject"), course: f.get("course") || null, total_marks: f.get("total_marks") },
      });
      router.push(`/exams/${exam.id}/paper`);
    } catch (err) {
      setError(err instanceof ClientError ? err.message : "Could not create the exam.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid gap-3 sm:grid-cols-5 sm:items-end" aria-label="Create an exam">
      <div className="flex flex-col gap-1.5 sm:col-span-2">
        <Label htmlFor="exam-name">Exam name</Label>
        <Input id="exam-name" name="name" required maxLength={300} placeholder="CIA 1" />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="exam-subject">Subject</Label>
        <Input id="exam-subject" name="subject" required maxLength={200} />
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="exam-total">Total marks</Label>
        <Input id="exam-total" name="total_marks" required inputMode="decimal" pattern="\d+(\.\d{1,2})?" />
      </div>
      <input type="hidden" name="course" value="" />
      <Button type="submit" disabled={busy}>
        Create exam
      </Button>
      {error ? (
        <Alert variant="destructive" className="sm:col-span-5">
          {error}
        </Alert>
      ) : null}
    </form>
  );
}
