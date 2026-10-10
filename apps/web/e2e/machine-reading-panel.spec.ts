import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";
import { ADMIN, createExam, drawBox, ok, question, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 3.3: the "Machine reading" panel. The machine-reading RESPONSE is intercepted at the network boundary so the panel can be
 * checked deterministically (a crafted low-confidence line, hostile text, a failed page, a failing endpoint). What the real API
 * returns is covered by the API tests; what the real engine reads is covered by the stack smoke and the 3.6 end-to-end test.
 */
type Mock = { mode: "lines" | "failed" | "error"; regionId: string; pageId: string };

const EVIL = "<img src=x onerror=window.__pwned=1> & <b>bold</b>";

function body(m: Mock) {
  const line = (id: string, text: string, score: number, low: boolean, y: number) => ({
    id,
    text,
    score,
    low_confidence: low,
    bbox: [0.1, y, 0.8, y + 0.03],
    overlap: 1,
  });
  return {
    notice: "Machine reading: it can be wrong, especially for handwriting. Always check it against the page.",
    low_confidence_below: 0.8,
    regions: [
      {
        region_id: m.regionId,
        qid: "q1",
        attempt_no: 1,
        crossed_out: false,
        page_id: m.pageId,
        page_no: 1,
        page_status: m.mode === "failed" ? "failed" : "read",
        lines:
          m.mode === "failed"
            ? []
            : [
                line("l1", "Photosynthesis needs light", 0.95, false, 0.1),
                line("l2", "photosyn?hesis nedes ligh", 0.41, true, 0.15),
                line("l3", `${EVIL}\u202E`, 0.9, false, 0.2), // hostile markup + a bidi override
              ],
      },
    ],
  };
}

let admin: { context: BrowserContext; page: Page };
let sid: string;
const mock: Mock = { mode: "lines", regionId: "", pageId: "" };

test.beforeAll(async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  admin = await signIn(browser, ADMIN);
  const examId = await createExam(admin.context.request);
  sid = await uploadBooklet(admin.context.request, examId, "MR-PANEL");
  await admin.page.route(/\/api\/proxy\/submissions\/[^/]+\/machine-reading/, async (route: Route) => {
    if (mock.mode === "error") return route.fulfill({ status: 500, json: { error: { code: "internal_error", message: "boom" } } });
    return route.fulfill({ json: mock.regionId ? body(mock) : { notice: "x", low_confidence_below: 0.8, regions: [] } });
  });
});

test.afterAll(async () => {
  await admin?.context.close();
});

test("the panel shows machine lines as escaped text, flags low confidence, and always says it can be wrong", async () => {
  const page = admin.page;
  await page.goto(`/submissions/${sid}`);
  await question(page, 1).click();
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.05, 0.05, 0.95, 0.3);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  const ws = await workspace(admin.context.request, sid);
  mock.regionId = ws.regions[0]!.id;
  const pages = await ok<{ id: string }[]>(await admin.context.request.get(`/api/proxy/submissions/${sid}/pages`), "pages");
  mock.pageId = pages[0]!.id;
  await page.reload();

  const panel = page.getByTestId("machine-reading-panel");
  await expect(panel.getByTestId("machine-reading-notice")).toContainText("can be wrong");
  const lines = panel.getByTestId("machine-line");
  await expect(lines).toHaveCount(3);
  await expect(lines.nth(0)).toHaveText("Photosynthesis needs light");
  await expect(lines.nth(0)).toHaveAttribute("data-low-confidence", "false");

  // low confidence is flagged by words and style, not by colour alone
  await expect(lines.nth(1)).toHaveAttribute("data-low-confidence", "true");
  await expect(lines.nth(1)).toContainText("check this line");
  await expect(panel.getByText("check this line")).toHaveCount(1);
  await expect(lines.nth(1)).toHaveAttribute("aria-label", /low confidence, check this line/);

  // hostile student text is DATA: shown literally, never parsed as markup, bidi override removed
  await expect(lines.nth(2)).toHaveText(EVIL);
  await expect(panel.locator("img, b")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
});

test("keyboard: focusing a line outlines it on the page, leaving removes the outline", async () => {
  const page = admin.page;
  const lines = page.getByTestId("machine-reading-panel").getByTestId("machine-line");
  await expect(page.getByTestId("line-highlight")).toHaveCount(0);
  await lines.nth(1).focus();
  await expect(page.getByTestId("line-highlight")).toBeVisible();
  const style = await page.getByTestId("line-highlight").getAttribute("style");
  expect(style).toContain("left: 10%");
  await page.keyboard.press("Tab"); // next line
  await expect(lines.nth(2)).toBeFocused();
  await page.getByRole("button", { name: "Hide machine reading" }).focus();
  await expect(page.getByTestId("line-highlight")).toHaveCount(0);
});

test("the panel can be hidden, and the choice is remembered", async () => {
  const page = admin.page;
  const panel = page.getByTestId("machine-reading-panel");
  await page.getByRole("button", { name: "Hide machine reading" }).click();
  await expect(panel.getByTestId("machine-line")).toHaveCount(0);
  await expect(panel.getByTestId("machine-reading-notice")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Show machine reading" })).toHaveAttribute("aria-pressed", "false");
  await page.reload();
  await expect(page.getByRole("button", { name: "Show machine reading" })).toBeVisible(); // still hidden after a reload
  await page.getByRole("button", { name: "Show machine reading" }).click();
  await expect(panel.getByTestId("machine-line")).toHaveCount(3);
});

test("a page that could not be read says so; a failing endpoint never blocks grading", async () => {
  const page = admin.page;
  mock.mode = "failed";
  await page.reload();
  const panel = page.getByTestId("machine-reading-panel");
  await expect(panel).toContainText("This page could not be machine-read. Read it from the page.");
  await expect(panel.getByTestId("machine-line")).toHaveCount(0);

  mock.mode = "error";
  await page.reload();
  await expect(panel).toContainText("Machine reading is not available right now");
  // grading works exactly as before
  await page.getByRole("radio", { name: /Full/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByText("Grade saved.")).toBeVisible();
  await expect(page.getByTestId("total")).toHaveText(/^\d+ \/ 5$/);
});
