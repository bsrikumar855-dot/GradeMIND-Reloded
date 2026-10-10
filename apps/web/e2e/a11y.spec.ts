import AxeBuilder from "@axe-core/playwright";
import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";
import { ADMIN, EXAMINER, createExam, ok, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.5: automated accessibility checks (axe-core, WCAG 2.0/2.1/2.2 A and AA rules) on the main pages and states, for an administrator and
 * for an examiner, at desktop and phone width. Any violation fails the test, and the message names the rule, the elements and a help URL.
 *
 * What this does NOT cover: screen-reader behaviour (announcements, reading order as spoken) and anything automated rules cannot judge
 * (whether labels make sense, whether the page is understandable). Those stay a listed manual gap.
 */
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function scan(page: Page, where: string): Promise<void> {
  await page.waitForLoadState("networkidle");
  // axe reads the colours that are on screen at that instant: a button half-way through its hover/toggle transition would be measured
  // at a colour it never rests at. Switch transitions off for the measurement (nothing else about the page changes).
  await page.addStyleTag({ content: "*, *::before, *::after { transition: none !important; animation: none !important; }" });
  const result = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const report = result.violations.map((v) => ({
    rule: v.id,
    impact: v.impact,
    help: v.help,
    url: v.helpUrl,
    nodes: v.nodes.slice(0, 4).map((n) => `${n.target.join(" ")} :: ${n.failureSummary?.split("\n").slice(0, 2).join(" ") ?? ""}`),
  }));
  expect(report, `${where}: ${report.length} accessibility violation(s)\n${JSON.stringify(report, null, 2)}`).toEqual([]);
}

const MR = (regionId: string, pageId: string) => ({
  notice: "Machine reading: it can be wrong, especially for handwriting. Always check it against the page.",
  low_confidence_below: 0.8,
  regions: [
    {
      region_id: regionId,
      qid: "q1",
      attempt_no: 1,
      crossed_out: false,
      page_id: pageId,
      page_no: 1,
      page_status: "read",
      lines: [
        { id: "l1", text: "Photosynthesis needs light", original_text: "Photosynthesis needs light", corrected: false, correction_id: null, score: 0.95, low_confidence: false, bbox: [0.1, 0.1, 0.8, 0.13], overlap: 1 },
        { id: "l2", text: "photosyn?hesis nedes ligh", original_text: "photosyn?hesis nedes ligh", corrected: false, correction_id: null, score: 0.41, low_confidence: true, bbox: [0.1, 0.15, 0.8, 0.18], overlap: 1 },
      ],
    },
  ],
});

let admin: { context: BrowserContext; page: Page };
let examiner: { context: BrowserContext; page: Page };
let examId: string;
let sid: string;

test.describe.configure({ mode: "serial" });
test.setTimeout(240_000);

test.beforeAll(async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  expect(EXAMINER.email && EXAMINER.password, "E2E_EXAMINER_EMAIL and E2E_EXAMINER_PASSWORD must be set").toBeTruthy();
  admin = await signIn(browser, ADMIN);
  examiner = await signIn(browser, EXAMINER);
  const api = admin.context.request;
  examId = await createExam(api);
  const me = await ok<{ id: string }>(await examiner.context.request.get("/api/proxy/me"), "examiner /me");
  expect((await api.post(`/api/proxy/exams/${examId}/assignments`, { data: { user_id: me.id } })).status()).toBe(204);
  sid = await uploadBooklet(api, examId, "A11Y-1");
  const ws = await workspace(api, sid);
  const region = await ok<{ id: string }>(
    await api.post(`/api/proxy/submissions/${sid}/regions`, { data: { page_id: ws.pages[0]!.id, bbox: [0.05, 0.05, 0.9, 0.25], qid: "q1" } }),
    "region",
  );
  await ok(await api.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: "part" } } }), "grade");
  // a deterministic machine reading, so the panel is on the page in every run (the real engine is covered elsewhere)
  for (const p of [admin.page, examiner.page]) {
    await p.route(/\/api\/proxy\/submissions\/[^/]+\/machine-reading/, (route: Route) => route.fulfill({ json: MR(region.id, ws.pages[0]!.id) }));
  }
});

test.afterAll(async () => {
  await admin?.context.close();
  await examiner?.context.close();
});

test("sign-in page", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/login");
  await scan(page, "/login");
  await ctx.close();
});

test("administrator: the pages around an exam", async () => {
  const p = admin.page;
  for (const path of ["/", "/exams", `/exams/${examId}/paper`, `/exams/${examId}/rubric`, `/exams/${examId}/submissions`, `/exams/${examId}/totals`, `/exams/${examId}/analytics`, `/exams/${examId}/audit`, `/exams/${examId}/examiners`, "/admin/users"]) {
    await p.goto(path);
    await scan(p, path);
  }
});

test("administrator: the grading workspace, drawing on, the correction editor, sign-off", async () => {
  const p = admin.page;
  await p.goto(`/submissions/${sid}`);
  await expect(p.getByTestId("machine-reading-panel")).toBeVisible();
  await scan(p, "workspace");
  await p.getByRole("button", { name: "Draw answer box" }).click();
  await scan(p, "workspace, drawing on");
  await p.getByRole("button", { name: "Drawing: on" }).click();
  await p.getByRole("button", { name: "Correct line 1" }).click();
  await scan(p, "workspace, correcting a machine line");
  await p.getByRole("button", { name: "Cancel" }).first().click();
  await p.getByRole("button", { name: "Shortcuts (?)" }).click();
  await scan(p, "workspace, shortcuts help open");
  // finalized, and the reopen form
  await ok(await admin.context.request.post(`/api/proxy/submissions/${sid}/finalize`, { data: { confirm_not_attempted: true } }), "finalize");
  await p.reload();
  await expect(p.getByTestId("finalize-panel")).toHaveAttribute("data-state", "FINALIZED");
  await scan(p, "workspace, finalized");
  await p.getByRole("button", { name: "Reopen…" }).click();
  await scan(p, "workspace, reopen form");
  await ok(await admin.context.request.post(`/api/proxy/submissions/${sid}/reopen`, { data: { reason: "accessibility check finished" } }), "reopen");
});

test("examiner: the pages an examiner can reach", async () => {
  const p = examiner.page;
  for (const path of ["/", "/exams", `/exams/${examId}/paper`, `/exams/${examId}/submissions`, `/exams/${examId}/totals`, `/submissions/${sid}`]) {
    await p.goto(path);
    await scan(p, `examiner ${path}`);
  }
});

test("phone width: no horizontal page scroll, and the pages still pass the checks", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });
  const p = await context.newPage();
  await p.goto("/login");
  await p.getByLabel("Email").fill(ADMIN.email);
  await p.getByLabel("Password").fill(ADMIN.password);
  await p.getByRole("button", { name: "Sign in" }).click();
  await expect(p).toHaveURL(/\/$/);
  for (const path of ["/", "/exams", `/exams/${examId}/submissions`, `/exams/${examId}/totals`, `/exams/${examId}/analytics`, "/admin/users", `/submissions/${sid}`]) {
    await p.goto(path);
    await p.waitForLoadState("networkidle");
    const over = await p.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }));
    expect(over.scroll, `${path}: the page is wider than a 375 px screen (${over.scroll} > ${over.client})`).toBeLessThanOrEqual(over.client + 1);
    await scan(p, `phone ${path}`);
  }
  await context.close();
});
