import { type NextRequest } from "next/server";
import { API_URL } from "@/lib/api";
import { isSameOrigin } from "@/lib/origin";
import { sessionToken } from "@/lib/session";

/**
 * Backend-for-frontend proxy: browser code calls /api/proxy/<api path> on the web origin; this handler forwards to the
 * API with the bearer token from the httpOnly session cookie, so the token never reaches browser JavaScript.
 * Mutating requests must be same-origin (CSRF). Bodies (uploads, CSV downloads, SSE) are streamed through.
 */
export const dynamic = "force-dynamic";

const PASS_REQUEST = ["content-type", "accept", "last-event-id"];
const PASS_RESPONSE = ["content-type", "content-disposition", "cache-control", "x-request-id"];

async function forward(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }, method: string): Promise<Response> {
  if (method !== "GET" && !isSameOrigin(req.headers.get("origin"), req.headers.get("host"))) {
    return new Response("Forbidden", { status: 403 });
  }
  const token = await sessionToken();
  if (!token) {
    return Response.json({ error: { code: "unauthenticated", message: "Sign in to continue." } }, { status: 401 });
  }
  const { path } = await ctx.params;
  if (path.some((p) => p === ".." || p.includes("/"))) return new Response("Bad request", { status: 400 });
  const url = `${API_URL}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers({ authorization: `Bearer ${token}` });
  for (const h of PASS_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const init: RequestInit & { duplex?: "half" } = { method, headers, cache: "no-store", redirect: "manual" };
  if (method !== "GET" && method !== "DELETE") {
    init.body = req.body;
    init.duplex = "half";
  }
  let upstream: Response;
  try {
    upstream = await fetch(url, init);
  } catch {
    return Response.json({ error: { code: "unavailable", message: "The service is not reachable right now." } }, { status: 502 });
  }
  const out = new Headers();
  for (const h of PASS_RESPONSE) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

type Ctx = { params: Promise<{ path: string[] }> };
export const GET = (req: NextRequest, ctx: Ctx) => forward(req, ctx, "GET");
export const POST = (req: NextRequest, ctx: Ctx) => forward(req, ctx, "POST");
export const PUT = (req: NextRequest, ctx: Ctx) => forward(req, ctx, "PUT");
export const DELETE = (req: NextRequest, ctx: Ctx) => forward(req, ctx, "DELETE");
