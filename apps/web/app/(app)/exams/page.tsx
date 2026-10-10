import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { NewExamForm } from "@/components/grading/new-exam";
import { apiGet, type Exam, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Exams" };

export default async function ExamsPage() {
  const [me, exams] = await Promise.all([apiGet<Me>("/api/me"), apiGet<Exam[]>("/api/exams")]);
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Exams</h1>
      {me.role !== "examiner" ? (
        <Card>
          <CardHeader>
            <CardTitle>New exam</CardTitle>
          </CardHeader>
          <CardContent>
            <NewExamForm />
          </CardContent>
        </Card>
      ) : null}
      {exams.length === 0 ? (
        <p className="text-muted-foreground">No exams yet.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="overflow-x-auto px-0">
            <table className="w-full text-sm">
              <caption className="sr-only">Exams</caption>
              <thead className="border-b bg-muted text-left">
                <tr>
                  <th scope="col" className="px-4 py-3 font-medium">Name</th>
                  <th scope="col" className="px-4 py-3 font-medium">Subject</th>
                  <th scope="col" className="px-4 py-3 font-medium">Course</th>
                  <th scope="col" className="px-4 py-3 text-right font-medium">Total marks</th>
                </tr>
              </thead>
              <tbody>
                {exams.map((e) => (
                  <tr key={e.id} className="border-b last:border-0">
                    <td className="px-4 py-3 font-medium">
                      <Link href={`/exams/${e.id}/paper`} className="text-primary underline underline-offset-4">
                        {e.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3">{e.subject}</td>
                    <td className="px-4 py-3 text-muted-foreground">{e.course ?? "—"}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{e.total_marks}</td>
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
