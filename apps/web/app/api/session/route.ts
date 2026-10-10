import { NextResponse, type NextRequest } from "next/server";
import { API_URL } from "@/lib/api";
import { isSameOrigin } from "@/lib/origin";
import { seeOther } from "@/lib/redirect";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req.headers.get("origin"), req.headers.get("host"))) return new NextResponse("Forbidden", { status: 403 });
  const form = await req.formData();
  const email = String(form.get("email") ?? "");
  const password = String(form.get("password") ?? "");
  // The API throttles sign-in per account and per source address. This server is the only caller it trusts, so it passes on the address it
  // was reached from: the last X-Forwarded-For value (the one Next, or a reverse proxy in front of it, wrote), or "unknown".
  const forwarded = (req.headers.get("x-forwarded-for") ?? "").split(",").pop()?.trim() || "unknown";
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json", "x-forwarded-for": forwarded },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
  } catch {
    return seeOther("/login?error=unavailable");
  }
  if (res.status === 401) return seeOther("/login?error=invalid");
  if (res.status === 429) return seeOther("/login?error=throttled");
  if (res.status === 422) return seeOther("/login?error=request");
  if (!res.ok) return seeOther("/login?error=unavailable");
  const { access_token, expires_in } = (await res.json()) as { access_token: string; expires_in: number };
  const out = seeOther("/");
  out.cookies.set(SESSION_COOKIE, access_token, sessionCookieOptions(expires_in));
  return out;
}
