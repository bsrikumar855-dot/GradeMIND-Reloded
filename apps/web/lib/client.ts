/** Browser-side API client: everything goes through the same-origin proxy (/api/proxy), never straight to the API. */

export type Issue = { path: string; code: string; message: string };

export class ClientError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public issues: Issue[] = [],
  ) {
    super(message);
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 204) return undefined as T;
  const body = (await res.json().catch(() => null)) as { error?: { code: string; message: string; issues?: Issue[] } } | null;
  if (!res.ok) {
    if (res.status === 401) window.location.assign("/login?expired=1");
    throw new ClientError(res.status, body?.error?.code ?? "error", body?.error?.message ?? "Something went wrong.", body?.error?.issues ?? []);
  }
  return body as T;
}

export async function api<T>(path: string, init: { method?: string; json?: unknown; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = { accept: "application/json" };
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers["content-type"] = "application/json";
    body = JSON.stringify(init.json);
  } else if (init.form) {
    body = init.form;
  }
  const res = await fetch(`/api/proxy/${path.replace(/^\//, "")}`, { method: init.method ?? "GET", headers, body, cache: "no-store" });
  return handle<T>(res);
}
