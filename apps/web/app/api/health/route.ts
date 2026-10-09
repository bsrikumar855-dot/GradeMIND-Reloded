export const dynamic = "force-dynamic";

/** Container health check for the web service itself (the API has its own /health endpoints). */
export function GET() {
  return Response.json({ status: "ok" });
}
