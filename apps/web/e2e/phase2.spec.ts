import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const EMAIL = process.env.E2E_EMAIL ?? "";
const PASSWORD = process.env.E2E_PASSWORD ?? "";
const BOOKLET = path.join(__dirname, "fixtures", "booklet.pdf");

async function drawBox(page: Page, x0: number, y0: number, x1: number, y1: number) {
  const box = await page.getByTestId("page-canvas").boundingBox();
  if (!box) throw new Error("page canvas not visible");
  await page.mouse.move(box.x + box.width * x0, box.y + box.height * y0);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * ((x0 + x1) / 2), box.y + box.height * ((y0 + y1) / 2), { steps: 5 });
  await page.mouse.move(box.x + box.width * x1, box.y + box.height * y1, { steps: 5 });
  await page.mouse.up();
}

test("Phase 2: paper → rubric → booklet → map → grade → totals → audit", async ({ page, request, baseURL }, info) => {
  expect(EMAIL && PASSWORD, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();

  // sign in
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/$/);

  // create the exam
  await page.goto("/exams");
  await page.getByLabel("Exam name").fill("E2E exam");
  await page.getByLabel("Subject").fill("Biology");
  await page.getByLabel("Total marks").fill("5");
  await page.getByRole("button", { name: "Create exam" }).click();
  await expect(page).toHaveURL(/\/exams\/[0-9a-f-]+\/paper/);
  const examUrl = page.url().replace(/\/paper$/, "");

  // question paper: paste, parse (rule-based), save, approve
  await page.getByLabel("Question paper text").fill("1. Define ecosystem. [2]\n2. Explain the water cycle. [3]");
  await page.getByRole("button", { name: "Parse into questions" }).click();
  await expect(page.getByRole("list", { name: "Questions" })).toBeVisible();
  await page.getByRole("button", { name: "Save draft" }).click();
  await page.getByRole("button", { name: "Approve structure" }).click();
  await expect(page.getByText("Approved v1")).toBeVisible();

  // rubric: the default two-level criterion per question, approve
  await page.goto(`${examUrl}/rubric`);
  await page.getByRole("button", { name: "Save draft" }).click();
  await page.getByRole("button", { name: "Approve rubric" }).click();
  await expect(page.getByText("Approved v1")).toBeVisible();

  // upload a booklet and wait for the worker to render its pages
  await page.goto(`${examUrl}/submissions`);
  await page.getByLabel("Student reference").fill("E2E-001");
  await page.getByLabel("Booklet (PDF, PNG or JPEG)").setInputFiles(BOOKLET);
  await page.getByRole("button", { name: "Upload" }).click();
  await expect(page.getByRole("link", { name: "Grade", exact: true })).toBeVisible({ timeout: 120_000 });
  await page.getByRole("link", { name: "Grade", exact: true }).click();
  try {
    await expect(page.getByTestId("total")).toHaveText("0 / 5");
  } catch (e) {
    // surface what the page actually showed (job logs are not always readable; the error message is)
    const text = (await page.locator("body").innerText()).slice(0, 600);
    const url = page.url();
    const sid = url.split("/").pop();
    const r = await page.request.get(`/api/proxy/submissions/${sid}/workspace`);
    throw new Error(`workspace did not render (${url}): ${text} || API ${r.status()} ${(await r.text()).slice(0, 500)}`, { cause: e });
  }

  // question 1: map the answer, then grade it (full marks)
  await page.getByRole("button", { name: /^1\b/ }).click();
  await page.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(page, 0.1, 0.1, 0.8, 0.3);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  await page.getByRole("radio", { name: /Full/ }).check();
  await page.getByRole("button", { name: "Save grade" }).click();
  await expect(page.getByText("Grade saved.")).toBeVisible();
  await expect(page.getByTestId("question-marks")).toHaveText("2 / 2");
  await expect(page.getByTestId("total")).toHaveText("2 / 5");

  // question 2: map the answer, grade it with the keyboard (level 1 = None → 0 marks), save with Ctrl+Enter
  await page.getByRole("button", { name: /^2\b/ }).click();
  await drawBox(page, 0.1, 0.4, 0.8, 0.6);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  await page.keyboard.press("1");
  await page.keyboard.press("Control+Enter");
  await expect(page.getByTestId("question-marks")).toHaveText("0 / 3");
  await page.screenshot({ path: info.outputPath("workspace.png"), fullPage: true });

  // totals: one row at 2 / 5, and the CSV carries the same numbers
  await page.goto(`${examUrl}/totals`);
  const row = page.getByRole("row", { name: /E2E-001/ });
  await expect(row).toContainText("2");
  await expect(row).toContainText("/5");
  const csv = await page.context().request.get(`${baseURL}/api/proxy/exams/${examUrl.split("/").pop()}/totals.csv`);
  expect(csv.ok()).toBeTruthy();
  const csvText = await csv.text();
  expect(csvText).toContain("E2E-001");
  expect(csvText.split("\n")[1]).toMatch(/E2E-001,.*,2,5,yes/);

  // audit trail: every write above is there, in order
  await page.goto(`${examUrl}/audit`);
  const table = page.getByRole("table");
  await expect(table).toContainText("region.create");
  await expect(table).toContainText("evaluation.save");
  await expect(table).toContainText("totals.export_csv");
  const actions = await table.locator("tbody tr td:nth-child(2)").allInnerTexts();
  console.log("AUDIT ACTIONS:\n" + actions.join("\n"));
  expect(actions.filter((a) => a === "region.create")).toHaveLength(2);
  expect(actions.filter((a) => a === "evaluation.save")).toHaveLength(2);
  await page.screenshot({ path: info.outputPath("audit.png"), fullPage: true });
  void request;
});
