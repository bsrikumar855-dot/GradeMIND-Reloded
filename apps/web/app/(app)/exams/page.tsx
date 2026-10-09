import type { Metadata } from "next";
import { Card, CardContent } from "@/components/ui/card";
import { apiGet, type Exam } from "@/lib/api";

export const metadata: Metadata = { title: "Exams" };

export default async function ExamsPage() {
  const exams = await apiGet<Exam[]>("/api/exams");
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Exams</h1>
      {exams.length === 0 ? (
        <p className="text-muted-foreground">No exams yet.</p>
      ) : (
        <Card className="py-0">
          <CardContent className="px-0">
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
                    <td className="px-4 py-3 font-medium">{e.name}</td>
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
