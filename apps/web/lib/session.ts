import "server-only";
import { cookies } from "next/headers";

/** The API access token lives only in an httpOnly cookie: browser JavaScript never sees it (spec §16). */
export const SESSION_COOKIE = "gm_session";

export async function sessionToken(): Promise<string | null> {
  return (await cookies()).get(SESSION_COOKIE)?.value ?? null;
}

export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.GRADEMIND_COOKIE_SECURE === "true",
    path: "/",
    maxAge: maxAgeSeconds,
  };
}
