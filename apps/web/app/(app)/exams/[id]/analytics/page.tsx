import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AnalyticsView } from "@/components/grading/analytics-view";
import { Alert } from "@/components/ui/alert";
import { ApiError, apiGet, type Me } from "@/lib/api";
import type { AnalyticsData } from "@/lib/types";

export const metadata: Metadata = { title: "Analytics" };

export default async function AnalyticsPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ scope?: string }>;
}) {
  const { id } = await params;
  const { scope } = await searchParams;
  const me = await apiGet<Me>("/api/me");
  if (me.role === "examiner") redirect(`/exams/${id}/submissions`);
  let data: AnalyticsData;
  try {
    data = await apiGet<AnalyticsData>(`/api/exams/${id}/analytics?scope=${scope === "finalized" ? "finalized" : "all"}`);
  } catch (e) {
    if (e instanceof ApiError && e.code === "rubric_not_approved") return <Alert>{e.message}</Alert>;
    throw e;
  }
  return <AnalyticsView examId={id} data={data} />;
}
