import { expect, test, type APIRequestContext, type Browser, type BrowserContext, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

/**
 * 3.0: the Phase 2 browser-coverage gaps. Setup (exam, paper, rubric, booklets, an examiner and their assignment) goes
 * through the API; the behaviour under test goes through the UI: keyboard-only box drawing, override-with-reason as an
 * examiner, several attempts, crossed-out, removing a region, and the N / P / ? shortcuts.
 */
const ADMIN = { email: process.env.E2E_EMAIL ?? "", password: process.env.E2E_PASSWORD ?? "" };
const EXAMINER = { email: process.env.E2E_EXAMINER_EMAIL ?? "", password: process.env.E2E_EXAMINER_PASSWORD ?? "" };
const BOOKLET = path.join(__dirname, "fixtures", "booklet.pdf");

type Region = { id: string; qid: string; attempt_no: number; crossed_out: boolean; bbox: number[] };
type Workspace = { regions: Region[]; score: { total: string; flags: string[] }; evaluations: { qid: string; is_override: boolean }[] };

async function signIn(browser: Browser, who: { email: string; password: string }): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(who.email);
  await page.getByLabel("Password").fill(who.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);
  return { context, page };
}

async function ok<T>(res: { ok(): boolean; status(): number; text(): Promise<string>; json(): Promise<unknown> }, what: string): Promise<T> {
  if (!res.ok()) throw new Error(`${what}: HTTP ${res.status()} ${(await res.text()).slice(0, 400)}`);
  return (await res.json()) as T;
}

const level = (id: string, marks: string, definition = "") => ({ id, name: id[0]!.toUpperCase() + id.slice(1), marks, definition });

async function createExam(api: APIRequestContext): Promise<string> {
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

async function uploadBooklet(api: APIRequestContext, examId: string, ref: string): Promise<string> {
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

const workspace = async (api: APIRequestContext, sid: string) => ok<Workspace>(await api.get(`/api/proxy/submissions/${sid}/workspace`), "workspace");

async function drawBox(page: Page, x0: number, y0: number, x1: number, y1: number) {
  const box = await page.getByTestId("page-canvas").boundingBox();
  if (!box) throw new Error("page canvas not visible");
  await page.mouse.move(box.x + box.width * x0, box.y + box.height * y0);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * ((x0 + x1) / 2), box.y + box.height * ((y0 + y1) / 2), { steps: 4 });
  await page.mouse.move(box.x + box.width * x1, box.y + box.height * y1, { steps: 4 });
  await page.mouse.up();
}

const question = (page: Page, n: number) => page.getByRole("list", { name: "Questions" }).getByRole("button", { name: new RegExp(`^${n}\\b`) });

test.describe.configure({ mode: "serial" });

let admin: { context: BrowserContext; page: Page };
let examiner: { context: BrowserContext; page: Page };
let examId: string;

test.beforeAll(async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  expect(EXAMINER.email && EXAMINER.password, "E2E_EXAMINER_EMAIL and E2E_EXAMINER_PASSWORD must be set (the examiner is created by `cli create-user`)").toBeTruthy();
  admin = await signIn(browser, ADMIN);
  examiner = await signIn(browser, EXAMINER);
  examId = await createExam(admin.context.request);
  const me = await ok<{ id: string; role: string }>(await examiner.context.request.get("/api/proxy/me"), "examiner /me");
  expect(me.role).toBe("examiner");
  const r = await admin.context.request.post(`/api/proxy/exams/${examId}/assignments`, { data: { user_id: me.id } });
  expect(r.status(), await r.text()).toBe(204);
});

test.afterAll(async () => {
  await admin?.context.close();
  await examiner?.context.close();
});

test("an unassigned exam is invisible to the examiner, an assigned one is not", async () => {
  const other = await createExam(admin.context.request); // not assigned to the examiner
  expect((await examiner.context.request.get(`/api/proxy/exams/${other}`)).status()).toBe(404);
  expect((await examiner.context.request.get(`/api/proxy/exams/${examId}`)).status()).toBe(200);
});

test("keyboard only: place, move, resize and confirm an answer box (and Escape cancels)", async () => {
  const sid = await uploadBooklet(admin.context.request, examId, "KB-1");
  const page = examiner.page;
  await page.goto(`/submissions/${sid}`);
  await expect(page.getByTestId("total")).toHaveText("0 / 5");
  await question(page, 1).focus();
  await page.keyboard.press("Enter"); // select question 1 with the keyboard

  const toggle = page.getByRole("button", { name: "Draw answer box" });
  await toggle.focus();
  await page.keyboard.press("Enter"); // drawing on: focus moves to the page
  await expect(page.getByTestId("page-canvas")).toBeFocused();
  await expect(page.getByTestId("draw-status")).toContainText("press Enter to place a box");

  // Escape cancels a box in progress: nothing is saved
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("kb-box")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("kb-box")).toHaveCount(0);
  expect((await workspace(examiner.context.request, sid)).regions).toHaveLength(0);

  // place, move (3 right, 2 down), resize (Shift: 2 wider), confirm
  await page.keyboard.press("Enter");
  for (let i = 0; i < 3; i++) await page.keyboard.press("ArrowRight");
  for (let i = 0; i < 2; i++) await page.keyboard.press("ArrowDown");
  for (let i = 0; i < 2; i++) await page.keyboard.press("Shift+ArrowRight");
  await expect(page.getByTestId("draw-status")).toContainText("16% from the left, 14% from the top, 44% wide, 20% high");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Answer area saved.")).toBeVisible();

  const [region] = (await workspace(examiner.context.request, sid)).regions;
  expect(region).toBeDefined();
  expect(region!.qid).toBe("q1");
  expect(region!.bbox.map((v) => Math.round(v * 100))).toEqual([16, 14, 60, 34]);
  await expect(page.getByTestId("kb-box")).toHaveCount(0);
});

test("override: a second examiner's change needs a reason and keeps history", async () => {
  const sid = await uploadBooklet(admin.context.request, examId, "OV-1");
  const page = examiner.page;
  await page.goto(`/submissions/${sid}`);
  await question(page, 1).click();
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.1, 0.1, 0.8, 0.3);
  await expect(page.getByText("Answer area saved.")).toBeVisible();

  // the admin grades the answer first (full marks)
  const first = await admin.context.request.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: "full" }, notes: "admin" } });
  expect(first.ok(), await first.text()).toBeTruthy();

  await page.reload();
  await question(page, 1).click();
  await page.getByRole("radio", { name: /Part/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByText(/Another examiner graded this answer/)).toBeVisible(); // refused without a reason
  await expect(page.getByLabel(/Reason for changing another examiner/)).toBeVisible();

  await page.getByLabel(/Reason for changing another examiner/).fill("The definition leaves out the non-living part.");
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByText("Grade saved.")).toBeVisible();
  await expect(page.getByTestId("question-marks")).toHaveText("1 / 2");
  await expect(page.getByTestId("total")).toHaveText("1 / 5");

  const hist = await ok<{ is_override: boolean; marks: string; override_reason: string | null }[]>(
    await admin.context.request.get(`/api/proxy/submissions/${sid}/evaluations/q1/1/history`),
    "history",
  );
  expect(hist.map((h) => [h.is_override, h.marks])).toEqual([[false, "2"], [true, "1"]]);
  expect(hist[1]!.override_reason).toContain("non-living");

  await admin.page.goto(`/exams/${examId}/audit`);
  await expect(admin.page.getByRole("table")).toContainText("evaluation.override");
});

test("several attempts: the last counts; crossed-out is ignored; removing a graded region is flagged", async () => {
  const sid = await uploadBooklet(admin.context.request, examId, "AT-1");
  const page = examiner.page;
  await page.goto(`/submissions/${sid}`);
  await question(page, 2).click();
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.1, 0.1, 0.8, 0.3);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  await page.getByRole("radio", { name: /Full/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByTestId("total")).toHaveText("3 / 5");

  // the student answered question 2 again: new attempt, graded None; the last attempt counts
  await page.getByLabel(/Next box starts a new attempt/).check();
  await drawBox(page, 0.1, 0.4, 0.8, 0.6);
  await expect(page.getByRole("group", { name: "Attempts" })).toBeVisible();
  await page.getByRole("group", { name: "Attempts" }).getByRole("button", { name: "Attempt 2" }).click(); // the panel stays on attempt 1 until chosen
  await page.getByRole("radio", { name: /None/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByTestId("total")).toHaveText("0 / 5");
  await expect(page.getByText("multiple attempts")).toBeVisible();

  // crossing out attempt 2 makes attempt 1 count again
  const attempt2 = page.getByRole("listitem").filter({ hasText: "attempt 2" });
  // the checkbox only flips after the server round trip (it is controlled), so click and wait rather than .check()
  await attempt2.getByLabel("crossed out").click();
  await expect(attempt2.getByLabel("crossed out")).toBeChecked();
  await expect(page.getByTestId("total")).toHaveText("3 / 5");
  expect((await workspace(examiner.context.request, sid)).regions.find((r) => r.attempt_no === 2)?.crossed_out).toBe(true);
  await attempt2.getByLabel("crossed out").click();
  await expect(attempt2.getByLabel("crossed out")).not.toBeChecked();
  await expect(page.getByTestId("total")).toHaveText("0 / 5");

  // removing attempt 2's only region: attempt 1 counts again and the now-orphaned grade is flagged, not silently counted
  await attempt2.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("Answer area removed.")).toBeVisible();
  await expect(page.getByTestId("total")).toHaveText("3 / 5");
  await expect(page.getByText("orphaned evaluations")).toBeVisible();
  await expect(page.getByRole("group", { name: "Attempts" })).toHaveCount(0);
});

test("shortcuts: ? toggles help, N and P move between questions, mapped or not", async () => {
  const sid = await uploadBooklet(admin.context.request, examId, "SC-1");
  const page = examiner.page;
  await page.goto(`/submissions/${sid}`);
  await expect(page.getByTestId("total")).toHaveText("0 / 5");

  const help = page.getByText(/1–9 pick a level/);
  // the label and the marks are separate spans inside one button ("1" + "0 / 2"), so read the label span
  const current = page.getByRole("list", { name: "Questions" }).locator('[aria-current="true"] > span:first-child');

  // 1. with NO answer box anywhere (no grading panel on screen) the shortcuts already work
  await expect(help).toHaveCount(0);
  await page.keyboard.press("?");
  await expect(help).toBeVisible();
  await page.keyboard.press("?");
  await expect(help).toHaveCount(0);
  await page.keyboard.press("n");
  await expect(current).toHaveText("2");
  await page.keyboard.press("n"); // already the last question: stays
  await expect(current).toHaveText("2");
  await page.keyboard.press("P");
  await expect(current).toHaveText("1");
  await page.keyboard.press("p"); // already the first: stays
  await expect(current).toHaveText("1");

  // 2. regression: with an answer box on question 1, N lands on the unmapped question 2 and P must still bring us back
  // (the shortcuts used to live in the grading panel, which unmounts there, so the keyboard user was stranded)
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.1, 0.1, 0.8, 0.3);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  await expect(page.getByRole("region", { name: "Grade this answer" })).toBeVisible();
  await page.keyboard.press("n");
  await expect(current).toHaveText("2");
  await expect(page.getByRole("region", { name: "Grade this answer" })).toHaveCount(0);
  await page.keyboard.press("p");
  await expect(current).toHaveText("1");
  await page.keyboard.press("?");
  await expect(help).toBeVisible();
});
