import type { Metadata } from "next";
import { Totals } from "@/components/grading/totals";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Totals" };

export default async function TotalsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await apiGet<Me>("/api/me");
  return <Totals examId={id} canReport={me.role !== "examiner"} />;
}
