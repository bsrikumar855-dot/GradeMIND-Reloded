import type { Metadata } from "next";
import { Booklets } from "@/components/grading/booklets";
import { apiGet, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Booklets" };

export default async function SubmissionsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const me = await apiGet<Me>("/api/me");
  return <Booklets examId={id} canUpload={me.role !== "examiner"} />;
}
