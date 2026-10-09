import "server-only";
import { redirect } from "next/navigation";
import { sessionToken } from "@/lib/session";

/** Server-side API client. The API base URL is server configuration; nothing here reaches the browser. */
export const API_URL = process.env.GRADEMIND_API_URL ?? "http://127.0.0.1:8000";

export type Me = { id: string; email: string; display_name: string; role: "admin" | "examiner" | "teacher"; org_id: string };
export type Exam = { id: string; name: string; subject: string; course: string | null; total_marks: string };
export type Health = { name: string; ok: boolean };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

/** Authenticated GET. A missing or rejected session sends the user to the sign-in page. */
export async function apiGet<T>(path: string): Promise<T> {
  const token = await sessionToken();
  if (!token) redirect("/login");
  const res = await fetch(`${API_URL}${path}`, { headers: { authorization: `Bearer ${token}` }, cache: "no-store" });
  if (res.status === 401) redirect("/login?expired=1");
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as { error?: { code: string; message: string } } | null;
    throw new ApiError(res.status, body?.error?.code ?? "error", body?.error?.message ?? "Something went wrong.");
  }
  return (await res.json()) as T;
}

export async function healthSummary(): Promise<Health[]> {
  const parts = ["db", "redis", "storage", "ocr"] as const;
  return Promise.all(
    parts.map(async (name) => {
      try {
        const r = await fetch(`${API_URL}/health/${name}`, { cache: "no-store", signal: AbortSignal.timeout(5000) });
        return { name, ok: r.ok };
      } catch {
        return { name, ok: false };
      }
    }),
  );
}
