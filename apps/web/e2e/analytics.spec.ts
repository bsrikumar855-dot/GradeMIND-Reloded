import { expect, test, type APIRequestContext } from "@playwright/test";
import { ADMIN, EXAMINER, createExam, ok, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.3: analytics from verdicts only, and the n < 5 rule in the page. Setup goes through the API (five graded booklets, one at a time);
 * the page is checked after the fourth (counts, but no mean, median or percentage) and after the fifth (the statistics appear).
 */
test.setTimeout(240_000);

async function gradeQ1(api: APIRequestContext, sid: string, level: "none" | "part" | "full"): Promise<void> {
  const ws = await workspace(api, sid);
  await ok(await api.post(`/api/proxy/submissions/${sid}/regions`, { data: { page_id: ws.pages[0]!.id, bbox: [0.1, 0.1, 0.9, 0.4], qid: "q1" } }), "region");
  await ok(await api.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: level } } }), "grade");
}

test("analytics: counts under five booklets, statistics from five, nothing for examiners", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  expect(EXAMINER.email && EXAMINER.password, "E2E_EXAMINER_EMAIL and E2E_EXAMINER_PASSWORD must be set").toBeTruthy();
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;
  const page = admin.page;
  const examId = await createExam(api);
  const levels = ["full", "full", "part", "none", "full"] as const; // Q1 marks 2, 2, 1, 0, 2

  for (let i = 0; i < 4; i++) await gradeQ1(api, await uploadBooklet(api, examId, `AN-${i + 1}`), levels[i]!);
  await page.goto(`/exams/${examId}/analytics`);
  const q1 = page.getByTestId("analytics-question").first();
  await expect(q1).toContainText("4"); // n
  await expect(q1.locator("td").nth(0)).toHaveText("4");
  await expect(q1.locator("td").nth(1)).toContainText("n < 5"); // mean not shown
  await expect(q1.locator("td").nth(2)).toContainText("n < 5"); // median not shown
  await expect(q1.locator("td").nth(3)).not.toContainText("%"); // no percentage either
  await expect(page.getByTestId("override-rate")).toContainText("n < 5");

  await gradeQ1(api, await uploadBooklet(api, examId, "AN-5"), levels[4]!);
  await page.reload();
  const q1b = page.getByTestId("analytics-question").first();
  await expect(q1b.locator("td").nth(0)).toHaveText("5");
  await expect(q1b.locator("td").nth(1)).toHaveText("1.4"); // (2 + 2 + 1 + 0 + 2) / 5
  await expect(q1b.locator("td").nth(2)).toHaveText("2");
  await expect(q1b.locator("td").nth(3)).toContainText("3 (60%)");
  await expect(page.getByTestId("ungraded")).toContainText("Mapped but not fully graded: 0 answer(s)");
  await page.getByRole("link", { name: "Finalized only" }).click();
  await expect(page).toHaveURL(/scope=finalized/);
  await expect(page.getByTestId("analytics")).toContainText("In this view");
  await expect(page.getByTestId("analytics-question").first().locator("td").nth(0)).toHaveText("0"); // nothing is finalized yet

  // an examiner has no Analytics tab, is sent away from the URL, and the API refuses
  const examiner = await signIn(browser, EXAMINER);
  const me = await ok<{ id: string }>(await examiner.context.request.get("/api/proxy/me"), "examiner /me");
  expect((await api.post(`/api/proxy/exams/${examId}/assignments`, { data: { user_id: me.id } })).status()).toBe(204);
  await examiner.page.goto(`/exams/${examId}/paper`);
  await expect(examiner.page.getByRole("navigation", { name: "Exam sections" }).getByRole("link", { name: "Analytics" })).toHaveCount(0);
  await examiner.page.goto(`/exams/${examId}/analytics`);
  await expect(examiner.page).toHaveURL(new RegExp(`/exams/${examId}/submissions$`));
  expect((await examiner.context.request.get(`/api/proxy/exams/${examId}/analytics`)).status()).toBe(403);

  await examiner.context.close();
  await admin.context.close();
});
