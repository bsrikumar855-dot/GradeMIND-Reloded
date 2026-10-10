import type { Metadata } from "next";
import { Totals } from "@/components/grading/totals";

export const metadata: Metadata = { title: "Totals" };

export default async function TotalsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Totals examId={id} />;
}
