import type { Metadata } from "next";
import { RubricEditor } from "@/components/grading/rubric-editor";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Rubric" };

export default async function RubricPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await apiGet<Me>("/api/me");
  return <RubricEditor examId={id} canEdit={me.role !== "examiner"} />;
}
