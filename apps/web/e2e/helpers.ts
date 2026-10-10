import { expect, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/** Shared Playwright helpers: sign-in, API setup (exam, paper, rubric, booklets) and page interactions. */
export const ADMIN = { email: process.env.E2E_EMAIL ?? "", password: process.env.E2E_PASSWORD ?? "" };
export const EXAMINER = { email: process.env.E2E_EXAMINER_EMAIL ?? "", password: process.env.E2E_EXAMINER_PASSWORD ?? "" };
const BOOKLET = path.join(__dirname, "fixtures", "booklet.pdf");

export type Region = { id: string; qid: string; attempt_no: number; crossed_out: boolean; bbox: number[] };
export type Workspace = { regions: Region[]; score: { total: string; flags: string[] }; evaluations: { qid: string; is_override: boolean }[] };

export async function signIn(browser: Browser, who: { email: string; password: string }): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  return { context, page };
}

export async function ok<T>(res: { ok(): boolean; status(): number; text(): Promise<string>; json(): Promise<unknown> }, what: string): Promise<T> {
  if (!res.ok()) throw new Error(`${what}: HTTP ${res.status()} ${(await res.text()).slice(0, 400)}`);
  return (await res.json()) as T;
}

export const level = (id: string, marks: string, definition = "") => ({ id, name: id[0]!.toUpperCase() + id.slice(1), marks, definition });

export async function createExam(api: APIRequestContext): Promise<string> {
  const exam = await ok<{ id: string }>(await api.post("/api/proxy/exams", { data: { name: "E2E gaps", subject: "Biology", total_marks: "5" } }), "create exam");
  const id = exam.id;
  const paper = {
    total_marks: "5",
    questions: [
      { id: "q1", label: "1", text: "Define ecosystem.", max_marks: "2" },
      { id: "q2", label: "2", text: "Explain the water cycle.", max_marks: "3" },
    ],
  };
  await ok(await api.put(`/api/proxy/exams/${id}/paper/draft`, { data: { document: paper } }), "save paper");
  await ok(await api.post(`/api/proxy/exams/${id}/paper/approve`), "approve paper");
  const rubric = {
    questions: [
      { qid: "q1", criteria: [{ id: "c1", name: "Definition", levels: [level("none", "0"), level("part", "1", "half of the definition"), level("full", "2")] }] },
      { qid: "q2", criteria: [{ id: "c1", name: "Cycle", levels: [level("none", "0"), level("full", "3")] }] },
    ],
  };
  await ok(await api.put(`/api/proxy/exams/${id}/rubric/draft`, { data: { document: rubric } }), "save rubric");
  await ok(await api.post(`/api/proxy/exams/${id}/rubric/approve`), "approve rubric");
  return id;
}

export async function uploadBooklet(api: APIRequestContext, examId: string, ref: string): Promise<string> {
  const up = await ok<{ id: string; job_id: string }>(
    await api.post(`/api/proxy/exams/${examId}/submissions`, {
      // the API refuses a byte-identical booklet within one exam, so every upload carries a unique trailing PDF comment
      multipart: { student_ref: ref, file: { name: "booklet.pdf", mimeType: "application/pdf", buffer: Buffer.concat([fs.readFileSync(BOOKLET), Buffer.from(`\n% ${ref} ${Date.now()}\n`)]) } },
    }),
    "upload booklet",
  );
  // grading only needs the page images: wait for those, never for the (slow, optional) machine reading
  await expect
    .poll(
      async () => {
        const rows = await ok<{ id: string; pages_ready: boolean }[]>(await api.get(`/api/proxy/exams/${examId}/submissions?limit=100`), "submissions");
        return rows.find((r) => r.id === up.id)?.pages_ready ?? false;
      },
      { timeout: 120_000, message: "pages were not rendered" },
    )
    .toBe(true);
  return up.id;
}

export const workspace = async (api: APIRequestContext, sid: string) => ok<Workspace>(await api.get(`/api/proxy/submissions/${sid}/workspace`), "workspace");

export async function drawBox(page: Page, x0: number, y0: number, x1: number, y1: number) {
  const box = await page.getByTestId("page-canvas").boundingBox();
  if (!box) throw new Error("page canvas not visible");
  await page.mouse.move(box.x + box.width * x0, box.y + box.height * y0);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * ((x0 + x1) / 2), box.y + box.height * ((y0 + y1) / 2), { steps: 4 });
  await page.mouse.move(box.x + box.width * x1, box.y + box.height * y1, { steps: 4 });
  await page.mouse.up();
}

export const question = (page: Page, n: number) => page.getByRole("list", { name: "Questions" }).getByRole("button", { name: new RegExp(`^${n}\\b`) });

