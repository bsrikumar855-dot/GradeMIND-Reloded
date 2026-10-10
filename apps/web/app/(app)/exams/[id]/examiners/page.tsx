import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ExamExaminers } from "@/components/admin/exam-examiners";
import { apiGet, type Me } from "@/lib/api";
import type { UserRow } from "@/lib/types";

export const metadata: Metadata = { title: "Examiners" };

export default async function ExaminersPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await apiGet<Me>("/api/me");
  if (me.role !== "admin") redirect(`/exams/${id}/submissions`);
  const [assigned, users] = await Promise.all([apiGet<UserRow[]>(`/api/exams/${id}/assignments`), apiGet<UserRow[]>("/api/users")]);
  return <ExamExaminers examId={id} initialAssigned={assigned} initialUsers={users} />;
}
