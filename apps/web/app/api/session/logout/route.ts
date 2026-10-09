import { NextResponse, type NextRequest } from "next/server";
import { isSameOrigin } from "@/lib/origin";
import { seeOther } from "@/lib/redirect";
import { SESSION_COOKIE } from "@/lib/session";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req.headers.get("origin"), req.headers.get("host"))) return new NextResponse("Forbidden", { status: 403 });
  const out = seeOther("/login");
  out.cookies.delete(SESSION_COOKIE);
  return out;
}
