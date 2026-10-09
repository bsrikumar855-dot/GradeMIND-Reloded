import { NextResponse } from "next/server";

/** 303 with a RELATIVE Location. Absolute URLs built from the request inside the container point at the bind address
 * (http://0.0.0.0:3000), not at the host the browser used; a relative Location is resolved by the browser itself. */
export function seeOther(path: `/${string}`): NextResponse {
  return new NextResponse(null, { status: 303, headers: { location: path } });
}
