import type { Metadata } from "next";
import { Workspace } from "@/components/grading/workspace";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Grade booklet" };

export default async function SubmissionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  const me = await apiGet<Me>("/api/me");
  return <Workspace submissionId={sid} canManage={me.role !== "examiner"} />;
}
