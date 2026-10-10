import { redirect } from "next/navigation";

export default async function ExamIndex({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/exams/${id}/paper`);
}
