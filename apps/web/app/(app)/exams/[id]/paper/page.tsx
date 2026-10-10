import type { Metadata } from "next";
import { PaperEditor } from "@/components/grading/paper-editor";
import { apiGet, type Exam, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Question paper" };

export default async function PaperPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [exam, me] = await Promise.all([apiGet<Exam>(`/api/exams/${id}`), apiGet<Me>("/api/me")]);
  return <PaperEditor examId={id} examTotal={exam.total_marks} canEdit={me.role !== "examiner"} />;
}
