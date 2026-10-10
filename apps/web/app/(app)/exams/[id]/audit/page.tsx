import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AuditTrail } from "@/components/grading/audit-trail";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Audit trail" };

export default async function AuditPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await apiGet<Me>("/api/me");
  if (me.role === "examiner") redirect(`/exams/${id}/submissions`);
  return <AuditTrail examId={id} />;
}
