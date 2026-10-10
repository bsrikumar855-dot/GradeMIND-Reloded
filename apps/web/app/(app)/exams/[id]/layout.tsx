import { ExamTabs } from "@/components/grading/exam-tabs";
import { apiGet, type Exam, type Me } from "@/lib/api";

export default async function ExamLayout({ children, params }: { children: React.ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [exam, me] = await Promise.all([apiGet<Exam>(`/api/exams/${id}`), apiGet<Me>("/api/me")]);
  const tabs = [
    { slug: "paper", label: "Question paper" },
    { slug: "rubric", label: "Rubric" },
    { slug: "submissions", label: "Booklets" },
    { slug: "totals", label: "Totals" },
    ...(me.role !== "examiner" ? [{ slug: "audit", label: "Audit trail" }] : []),
    ...(me.role === "admin" ? [{ slug: "examiners", label: "Examiners" }] : []),
  ];
  return (
    <div className="flex flex-col gap-6">
      <div>
        <p className="text-sm text-muted-foreground">
          {exam.subject} · {exam.total_marks} marks
        </p>
        <h1 className="text-2xl font-semibold">{exam.name}</h1>
      </div>
      <ExamTabs examId={id} tabs={tabs} />
      <div>{children}</div>
    </div>
  );
}
