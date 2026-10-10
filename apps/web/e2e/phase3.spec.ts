import { expect, test } from "@playwright/test";
import { ADMIN, createExam, drawBox, ok, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 3.6: the whole Phase 3 flow with the REAL OCR engine (nothing mocked, nothing faked).
 *
 * upload a booklet -> the worker renders pages and machine-reads them -> machine lines appear in the answer box -> the examiner
 * corrects one -> the correction, its history and the audit trail are all there -> grading is exactly what it was.
 *
 * The page is a SYNTHETIC, PRINTED page (three lines of Helvetica text). What the engine reads here says nothing about handwriting:
 * Phase 0b measured a best line error of about 0.43 on real handwritten sheets, and nothing in this test changes that.
 */
type Reading = {
  notice: string;
  regions: { region_id: string; page_status: string; lines: { id: string; text: string; original_text: string; score: number | null; corrected: boolean; correction_id: string | null }[] }[];
};
type History = { original_text: string; corrections: { id: string; corrected_text: string; supersedes_id: string | null; examiner_id: string }[] };

test.setTimeout(420_000);

test("Phase 3: machine lines in the answer box, a correction, its audit trail, and grading unchanged", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;
  const page = admin.page;

  // --- a booklet with three known printed lines on page 1 ---
  const examId = await createExam(api);
  const sid = await uploadBooklet(api, examId, "P3-001", "phase3-booklet.pdf");
  await page.goto(`/submissions/${sid}`);
  await expect(page.getByTestId("total")).toHaveText("0 / 5");

  // --- grade FIRST, while the machine reading may still be running: grading must not wait for it ---
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.03, 0.03, 0.9, 0.13); // the top of the page: the three printed lines
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  await page.getByRole("radio", { name: /Full/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByText("Grade saved.")).toBeVisible();
  await expect(page.getByTestId("total")).toHaveText("2 / 5");

  const scoreBefore = (await workspace(api, sid)).score;
  const csvBefore = await (await api.get(`/api/proxy/exams/${examId}/totals.csv`)).text();
  expect(scoreBefore.total).toBe("2");
  expect(csvBefore).toContain("P3-001");

  // --- the real engine reads the page (about 8-15 s per page on a CPU) ---
  await expect
    .poll(async () => (await ok<Reading>(await api.get(`/api/proxy/submissions/${sid}/machine-reading`), "machine reading")).regions[0]?.page_status, {
      timeout: 300_000,
      message: "the OCR stage did not finish",
    })
    .toBe("read");
  await page.reload();

  const panel = page.getByTestId("machine-reading-panel");
  await expect(panel.getByTestId("machine-reading-notice")).toContainText("can be wrong");
  const lines = panel.getByTestId("machine-line");
  const n = await lines.count();
  expect(n).toBeGreaterThanOrEqual(2); // machine lines are shown beside the answer box
  const read = await ok<Reading>(await api.get(`/api/proxy/submissions/${sid}/machine-reading`), "machine reading");
  const shown = read.regions[0]!.lines.map((l) => ({ text: l.text, score: l.score }));
  // evidence for the report: what the real engine read on the synthetic page (also visible as a CI annotation)
  console.log(`::notice title=Phase 3 OCR read (synthetic printed page, NOT handwriting)::${shown.map((l) => `${JSON.stringify(l.text)} (${l.score})`).join(" | ")}`);
  expect(shown.map((l) => l.text).join(" ").toLowerCase()).toContain("photosynthesis");

  // --- the examiner corrects the first line ---
  const first = read.regions[0]!.lines[0]!;
  await page.getByRole("button", { name: "Correct line 1" }).click();
  const fixed = `${first.text} [E2E]`;
  await page.getByLabel("Corrected text for line 1").fill(fixed);
  await page.getByRole("button", { name: "Save correction" }).click();
  await expect(lines.nth(0).getByTestId("machine-line-text")).toHaveText(fixed);
  await expect(lines.nth(0)).toHaveAttribute("data-corrected", "true");
  await lines.nth(0).getByRole("button", { name: "Show original" }).click();
  await expect(lines.nth(0).getByTestId("machine-line-original")).toContainText(first.text); // the machine's text is never lost

  // --- the corrections table (through the history endpoint) and the audit trail ---
  const hist = await ok<History>(await api.get(`/api/proxy/submissions/${sid}/ocr-lines/${first.id}/corrections`), "history");
  expect(hist.original_text).toBe(first.text);
  expect(hist.corrections).toHaveLength(1);
  expect(hist.corrections[0]).toMatchObject({ corrected_text: fixed, supersedes_id: null });
  await page.goto(`/exams/${examId}/audit`);
  const actions = await page.getByRole("table").locator("tbody tr td:nth-child(2)").allInnerTexts();
  console.log(`::notice title=Phase 3 audit trail (oldest first)::${actions.join(" | ")}`);
  expect(actions).toContain("line.correct");
  expect(actions.indexOf("evaluation.save")).toBeLessThan(actions.indexOf("line.correct"));
  expect(actions.filter((a) => a === "line.correct")).toHaveLength(1);

  // --- grading is exactly what it was before the machine read anything and before the correction ---
  const scoreAfter = (await workspace(api, sid)).score;
  expect(scoreAfter).toEqual(scoreBefore);
  const csvAfter = await (await api.get(`/api/proxy/exams/${examId}/totals.csv`)).text();
  expect(csvAfter).toBe(csvBefore);
  await page.goto(`/submissions/${sid}`);
  await expect(page.getByTestId("total")).toHaveText("2 / 5");

  await admin.context.close();
});
