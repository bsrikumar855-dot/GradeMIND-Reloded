import type { Metadata } from "next";
import { Workspace } from "@/components/grading/workspace";

export const metadata: Metadata = { title: "Grade booklet" };

export default async function SubmissionPage({ params }: { params: Promise<{ sid: string }> }) {
  const { sid } = await params;
  return <Workspace submissionId={sid} />;
}
