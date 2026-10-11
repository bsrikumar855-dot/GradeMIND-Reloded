import { NextResponse, type NextRequest } from "next/server";
import { API_URL } from "@/lib/api";
import { isSameOrigin } from "@/lib/origin";
import { seeOther } from "@/lib/redirect";
import { SESSION_COOKIE, sessionCookieOptions, sessionToken } from "@/lib/session";

/** Self-service password change (D30.3). A plain form post, like sign-in; the API answers with a fresh token because the change ends every
 *  other session, and this one is replaced here (the token never reaches page JavaScript). */
export async function POST(req: NextRequest) {
  if (!isSameOrigin(req.headers.get("origin"), req.headers.get("host"))) return new NextResponse("Forbidden", { status: 403 });
  const token = await sessionToken();
  if (!token) return seeOther("/login");
  const form = await req.formData();
  const current = String(form.get("current_password") ?? "");
  const next = String(form.get("new_password") ?? "");
  const again = String(form.get("new_password_again") ?? "");
  if (next !== again) return seeOther("/account/password?error=mismatch");
  const forwarded = (req.headers.get("x-forwarded-for") ?? "").split(",").pop()?.trim() || "unknown";
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/auth/change-password`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}`, "x-forwarded-for": forwarded },
      body: JSON.stringify({ current_password: current, new_password: next }),
      cache: "no-store",
    });
  } catch {
    return seeOther("/account/password?error=unavailable");
  }
  if (res.status === 401) return seeOther("/login?expired=1");
  if (res.status === 403) return seeOther("/account/password?error=current");
  if (res.status === 429) return seeOther("/account/password?error=throttled");
  if (res.status === 422) {
    const body = (await res.json().catch(() => null)) as { error?: { code?: string } } | null;
    return seeOther(`/account/password?error=${body?.error?.code === "weak_password" ? "weak" : "request"}`);
  }
  if (!res.ok) return seeOther("/account/password?error=unavailable");
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  const out = seeOther("/account/password?done=1");
  out.cookies.set(SESSION_COOKIE, access_token, sessionCookieOptions(expires_in));
  return out;
}
