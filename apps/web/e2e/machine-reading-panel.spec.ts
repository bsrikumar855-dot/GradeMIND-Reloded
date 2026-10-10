import { expect, test, type BrowserContext, type Page, type Route } from "@playwright/test";
import { ADMIN, createExam, drawBox, ok, question, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 3.3: the "Machine reading" panel. The machine-reading RESPONSE is intercepted at the network boundary so the panel can be
 * checked deterministically (a crafted low-confidence line, hostile text, a failed page, a failing endpoint). What the real API
 * returns is covered by the API tests; what the real engine reads is covered by the stack smoke and the 3.6 end-to-end test.
 */
type Mock = { mode: "lines" | "failed" | "error"; regionId: string; pageId: string };
type Fix = { id: string; text: string };

const EVIL = "<img src=x onerror=window.__pwned=1> & <b>bold</b>";
const ORIGINAL: Record<string, { text: string; score: number; low: boolean; y: number }> = {
  l1: { text: "Photosynthesis needs light", score: 0.95, low: false, y: 0.1 },
  l2: { text: "photosyn?hesis nedes ligh", score: 0.41, low: true, y: 0.15 },
  l3: { text: `${EVIL}\u202E`, score: 0.9, low: false, y: 0.2 }, // hostile markup + a bidi override
};

/** What the (mocked) server knows: current corrections, every PUT it received, and how the next PUT should answer. */
const server = { fixes: {} as Record<string, Fix>, puts: [] as { lineId: string; body: { text: string; expected_correction_id: string | null } }[], conflictNext: false, n: 0 };

function body(m: Mock) {
  const line = (id: string) => {
    const o = ORIGINAL[id]!;
    const fix = server.fixes[id];
    return {
      id,
      text: fix ? fix.text : o.text,
      original_text: o.text,
      corrected: !!fix,
      correction_id: fix ? fix.id : null,
      score: o.score,
      low_confidence: !fix && o.low,
      bbox: [0.1, o.y, 0.8, o.y + 0.03],
      overlap: 1,
    };
  };
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
        lines: m.mode === "failed" ? [] : ["l1", "l2", "l3"].map(line),
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
  await admin.page.route(/\/api\/proxy\/submissions\/[^/]+\/ocr-lines\/[^/]+\/correction/, async (route: Route) => {
    const lineId = route.request().url().split("/ocr-lines/")[1]!.split("/")[0]!;
    const sent = route.request().postDataJSON() as { text: string; expected_correction_id: string | null };
    server.puts.push({ lineId, body: sent });
    if (server.conflictNext) {
      server.conflictNext = false;
      server.fixes[lineId] = { id: `fix-${++server.n}`, text: "changed by someone else" }; // the other examiner got there first
      return route.fulfill({ status: 409, json: { error: { code: "line_changed", message: "Someone changed this line since you opened it. Reload and try again." } } });
    }
    const current = server.fixes[lineId];
    if ((current?.id ?? null) !== sent.expected_correction_id) {
      return route.fulfill({ status: 409, json: { error: { code: "line_changed", message: "stale" } } });
    }
    server.fixes[lineId] = { id: `fix-${++server.n}`, text: sent.text };
    return route.fulfill({ json: { id: lineId, text: sent.text, original_text: ORIGINAL[lineId]!.text, corrected: true, correction_id: server.fixes[lineId]!.id } });
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
  await expect(lines.nth(0).getByTestId("machine-line-text")).toHaveText("Photosynthesis needs light");
  await expect(lines.nth(0)).toHaveAttribute("data-low-confidence", "false");

  // low confidence is flagged by words and style, not by colour alone
  await expect(lines.nth(1)).toHaveAttribute("data-low-confidence", "true");
  await expect(lines.nth(1)).toContainText("check this line");
  await expect(panel.getByText("check this line")).toHaveCount(1);
  await expect(lines.nth(1)).toHaveAttribute("aria-label", /low confidence, check this line/);

  // hostile student text is DATA: shown literally, never parsed as markup, bidi override removed
  await expect(lines.nth(2).getByTestId("machine-line-text")).toHaveText(EVIL);
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
  await page.keyboard.press("Tab"); // into this line's own "Correct this line" button: the outline stays
  await expect(page.getByTestId("line-highlight")).toBeVisible();
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


test.describe.serial("correcting a line", () => {
  test.beforeEach(async () => {
    mock.mode = "lines";
    server.puts.length = 0;
    await admin.page.reload();
    await expect(admin.page.getByTestId("machine-reading-panel").getByTestId("machine-line")).toHaveCount(3);
  });

  test("editing saves the examiner's text, marks it corrected, drops the low-confidence flag, and keeps the original one click away", async () => {
    const page = admin.page;
    const lines = page.getByTestId("machine-reading-panel").getByTestId("machine-line");
    await page.getByRole("button", { name: "Correct line 2" }).click();
    const box = page.getByLabel("Corrected text for line 2");
    await expect(box).toBeFocused();
    await expect(box).toHaveValue("photosyn?hesis nedes ligh"); // prefilled with what is shown
    await expect(page.getByRole("button", { name: "Save correction" })).toBeDisabled(); // nothing changed yet
    await box.fill("photosynthesis needs light [?]");
    await page.getByRole("button", { name: "Save correction" }).click();

    await expect(lines.nth(1).getByTestId("machine-line-text")).toHaveText("photosynthesis needs light [?]");
    await expect(lines.nth(1)).toHaveAttribute("data-corrected", "true");
    await expect(lines.nth(1)).toHaveAttribute("data-low-confidence", "false"); // an examiner has looked at it
    await expect(lines.nth(1)).toContainText("corrected");
    await expect(lines.nth(1)).not.toContainText("check this line");
    expect(server.puts).toEqual([{ lineId: "l2", body: { text: "photosynthesis needs light [?]", expected_correction_id: null } }]);

    // the original is one click away, and the machine's text is never lost
    await expect(lines.nth(1).getByTestId("machine-line-original")).toHaveCount(0);
    await lines.nth(1).getByRole("button", { name: "Show original" }).click();
    await expect(lines.nth(1).getByTestId("machine-line-original")).toContainText("photosyn?hesis nedes ligh");
    await lines.nth(1).getByRole("button", { name: "Hide original" }).click();
    await expect(lines.nth(1).getByTestId("machine-line-original")).toHaveCount(0);
  });

  test("a second edit sends the correction it saw, so concurrent edits are detected", async () => {
    const page = admin.page;
    const seen = server.fixes["l2"]!.id; // the correction already on the line (from the previous test)
    await page.getByRole("button", { name: "Correct line 2" }).click();
    await page.getByLabel("Corrected text for line 2").fill("second version");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByTestId("machine-reading-panel").getByTestId("machine-line").nth(1).getByTestId("machine-line-text")).toHaveText("second version");
    expect(server.puts).toHaveLength(1);
    expect(server.puts[0]!.body.expected_correction_id).toBe(seen); // not null: it names the correction it saw
  });

  test("keyboard: Enter on a focused line opens the editor, Escape cancels without saving", async () => {
    const page = admin.page;
    const line = page.getByTestId("machine-reading-panel").getByTestId("machine-line").nth(0);
    await line.focus();
    await page.keyboard.press("Enter");
    const box = page.getByLabel("Corrected text for line 1");
    await expect(box).toBeFocused();
    await box.fill("never saved");
    await page.keyboard.press("Escape");
    await expect(box).toHaveCount(0);
    await expect(line).toBeFocused(); // focus returns to the line
    await expect(line.getByTestId("machine-line-text")).toHaveText("Photosynthesis needs light");
    expect(server.puts).toHaveLength(0);
    // typing in the box does not trigger the workspace shortcuts (n / p / ?)
    await page.keyboard.press("Enter");
    await page.getByLabel("Corrected text for line 1").fill("n p ? 1 2 3");
    await expect(page.getByText(/1–9 pick a level/)).toHaveCount(0);
    await page.keyboard.press("Escape");
  });

  test("a conflict shows a clear message and refreshes the line to what is there now", async () => {
    const page = admin.page;
    const line = page.getByTestId("machine-reading-panel").getByTestId("machine-line").nth(0);
    server.conflictNext = true;
    await page.getByRole("button", { name: "Correct line 1" }).click();
    await page.getByLabel("Corrected text for line 1").fill("my version");
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Someone changed this line" })).toBeVisible();
    // the editor stays open with the examiner's draft, and now shows what the line currently reads (refetched)
    await expect(page.getByLabel("Corrected text for line 1")).toHaveValue("my version");
    await expect(line.getByTestId("machine-line-current")).toContainText("changed by someone else");
    // saving again now names the correction it has seen, and succeeds
    const seen = server.fixes["l1"]!.id;
    await page.getByRole("button", { name: "Save correction" }).click();
    await expect(line.getByTestId("machine-line-text")).toHaveText("my version");
    expect(server.puts.at(-1)).toEqual({ lineId: "l1", body: { text: "my version", expected_correction_id: seen } });
  });

  test("hostile text is shown literally in the editor too, with invisible characters removed", async () => {
    const page = admin.page;
    await page.getByRole("button", { name: "Correct line 3" }).click();
    await expect(page.getByLabel("Corrected text for line 3")).toHaveValue(EVIL);
    await page.keyboard.press("Escape");
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  });
});
