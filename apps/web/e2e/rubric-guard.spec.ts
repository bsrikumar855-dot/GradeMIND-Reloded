import { expect, test } from "@playwright/test";
import { ADMIN, createExam, ok, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 5.1 (D30.9): after grading has started a new rubric version needs an administrator and a reason; the booklet that was finalized keeps its
 * version, its numbers and its result sheet; the open booklet shows that its earlier grade does not count under the new version.
 */
test.setTimeout(180_000);

test("a new rubric version after grading started: teacher blocked, admin with a reason, versions shown", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const stamp = Date.now();
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;
  const tpw = `Teacher-Pass-${stamp}-zz`;
  const temail = `rg.teacher.${stamp}@example.edu`;
  await ok(await api.post("/api/proxy/users", { data: { email: temail, display_name: "RG Teacher", role: "teacher", password: tpw } }), "teacher");
  const teacher = await signIn(browser, { email: temail, password: tpw });

  const examId = await createExam(api, `Rubric guard ${stamp}`);
  const grade = async (sid: string) => {
    const ws = await workspace(api, sid);
    await ok(await api.post(`/api/proxy/submissions/${sid}/regions`, { data: { page_id: ws.pages[0]!.id, bbox: [0.1, 0.1, 0.9, 0.3], qid: "q1" } }), "region");
    await ok(await api.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: "full" } } }), "grade");
  };
  const closed = await uploadBooklet(api, examId, `RG-A-${stamp}`);
  const open = await uploadBooklet(api, examId, `RG-B-${stamp}`);
  await grade(closed);
  await grade(open);
  expect((await api.post(`/api/proxy/submissions/${closed}/finalize`, { data: { confirm_not_attempted: true } })).status()).toBe(201);

  // --- the teacher sees why a new version is blocked and cannot approve it
  const tp = teacher.page;
  await tp.goto(`/exams/${examId}/rubric`);
  await expect(tp.getByTestId("rubric-locked")).toContainText("Grading has started");
  await tp.getByRole("button", { name: "Save draft" }).click();
  await expect(tp.getByText("Draft saved.")).toBeVisible();
  await expect(tp.getByRole("button", { name: /Approve new version/ })).toBeDisabled();
  expect((await teacher.context.request.post(`/api/proxy/exams/${examId}/rubric/approve`, { data: { reason: "I would really like a change" } })).status()).toBe(409);

  // --- the administrator needs a written reason
  const ap = admin.page;
  await ap.goto(`/exams/${examId}/rubric`);
  const approve = ap.getByRole("button", { name: /Approve new version/ });
  await expect(approve).toBeDisabled();
  await ap.getByLabel(/Why is a new rubric version needed/).fill("short");
  await expect(approve).toBeDisabled();
  await ap.getByLabel(/Why is a new rubric version needed/).fill("Criterion wording agreed in moderation.");
  await approve.click();
  await expect(ap.getByText(/New rubric version approved/)).toBeVisible();

  // --- the finalized booklet keeps rubric v1 and its numbers; the open one says its grade does not count under v2
  await ap.goto(`/submissions/${closed}`);
  await expect(ap.getByTestId("rubric-version")).toHaveText("Rubric v1");
  await expect(ap.getByTestId("rubric-version-note")).toContainText("finalized under rubric v1; the exam's rubric is now v2");
  await expect(ap.getByTestId("total")).toHaveText("2 / 5");
  await expect(ap.getByTestId("finalize-panel")).toHaveAttribute("data-state", "FINALIZED");
  await ap.goto(`/submissions/${open}`);
  await expect(ap.getByTestId("rubric-version")).toHaveText("Rubric v2");
  await expect(ap.getByTestId("older-grades-note")).toContainText("graded under an earlier rubric version");
  await expect(ap.getByTestId("total")).toHaveText("0 / 5");
  await ap.getByRole("radio", { name: /Part/ }).check();
  await ap.getByRole("button", { name: "Save grade" }).click();
  await expect(ap.getByText("Grade saved.")).toBeVisible();
  await expect(ap.getByTestId("graded-under")).toContainText("rubric v2");
  await expect(ap.getByTestId("total")).toHaveText("1 / 5");

  // --- totals say which version the finalized row is under; the audit trail has the reason
  await ap.goto(`/exams/${examId}/totals`);
  await expect(ap.getByRole("row", { name: new RegExp(`RG-A-${stamp}`) })).toContainText("rubric v1");
  await ap.goto(`/exams/${examId}/audit`);
  const cells = ap.getByRole("table").locator("tbody tr td:nth-child(2)");
  await expect.poll(async () => (await cells.allInnerTexts()).filter((a) => a === "rubric.approve").length, { timeout: 20_000 }).toBe(2);

  for (const x of [admin, teacher]) await x.context.close();
});
