import type { Metadata } from "next";
import Link from "next/link";
import { CheckCircle2, XCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet, healthSummary, type Exam, type Me } from "@/lib/api";

export const metadata: Metadata = { title: "Dashboard" };

const SERVICE_LABEL: Record<string, string> = { db: "Database", redis: "Job queue", storage: "File storage", ocr: "OCR service" };

export default async function DashboardPage() {
  const [me, exams, health] = await Promise.all([apiGet<Me>("/api/me"), apiGet<Exam[]>("/api/exams"), healthSummary()]);
  return (
    <div className="flex flex-col gap-8">
      <div>
        <h1 className="text-2xl font-semibold">Welcome, {me.display_name}</h1>
        <p className="text-muted-foreground">
          {me.role === "examiner" ? "Exams assigned to you are listed under Exams." : "Create exams and assign examiners under Exams."}
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Exams</CardTitle>
            <CardDescription>{me.role === "examiner" ? "Assigned to you" : "In your organisation"}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-semibold">{exams.length}</p>
            <Link href="/exams" className="text-sm text-primary underline underline-offset-4">
              View exams
            </Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>System status</CardTitle>
            <CardDescription>Checked when this page loaded</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-2">
              {health.map((h) => (
                <li key={h.name} className="flex items-center gap-2 text-sm">
                  {h.ok ? <CheckCircle2 className="size-4 text-success" aria-hidden="true" /> : <XCircle className="size-4 text-destructive" aria-hidden="true" />}
                  <span>{SERVICE_LABEL[h.name] ?? h.name}</span>
                  <span className={h.ok ? "text-success" : "text-destructive"}>{h.ok ? "Available" : "Unavailable"}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
