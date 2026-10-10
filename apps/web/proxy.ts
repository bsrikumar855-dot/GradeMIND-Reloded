import { NextResponse, type NextRequest } from "next/server";

/**
 * Per-request security headers (4.6). A fresh nonce goes into the Content-Security-Policy, so only the scripts Next itself renders can
 * run (the page cannot be made to run an injected script even if some text were ever rendered unescaped). Every page is dynamic
 * (`force-dynamic` in the root layout), which a per-request nonce requires.
 *
 * Booklet page images are served by the object store through short-lived signed URLs on its own origin, so that origin (taken from
 * GRADEMIND_S3_PUBLIC_ENDPOINT_URL) is allowed for images, and nothing else is. HSTS is sent only when the deployment is HTTPS
 * (GRADEMIND_COOKIE_SECURE=true), because sending it over plain HTTP would be ignored and over a misconfigured host could lock people out.
 */
function originOf(url: string | undefined): string {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
}

export function proxy(request: NextRequest) {
  const nonce = btoa(crypto.randomUUID());
  const dev = process.env.NODE_ENV === "development";
  const images = ["'self'", "blob:", "data:", originOf(process.env.GRADEMIND_S3_PUBLIC_ENDPOINT_URL)].filter(Boolean).join(" ");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'", // React style attributes (box positions) need it; a style cannot run script
    `img-src ${images}`,
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("Cross-Origin-Opener-Policy", "same-origin");
  if (process.env.GRADEMIND_COOKIE_SECURE === "true") response.headers.set("Strict-Transport-Security", "max-age=31536000");
  return response;
}

export const config = {
  matcher: [{ source: "/((?!_next/static|_next/image|favicon.ico).*)", missing: [{ type: "header", key: "next-router-prefetch" }] }],
};
