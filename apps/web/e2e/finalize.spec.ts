import { expect, test } from "@playwright/test";
import { ADMIN, EXAMINER, createExam, drawBox, ok, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.2: sign-off through the UI. An examiner grades; the administrator finalizes (confirming the question nobody answered), after which the
 * grades are read-only in the page, the API and the database; the examiner sees the state but has no buttons; the administrator reopens with a reason,
 * the examiner changes a grade, and finalizing again adds snapshot 2. The totals page and the audit trail show it all.
 */
test.setTimeout(180_000);

test("finalize, read-only, reopen with a reason, finalize again as snapshot 2", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  expect(EXAMINER.email && EXAMINER.password, "E2E_EXAMINER_EMAIL and E2E_EXAMINER_PASSWORD must be set").toBeTruthy();
  const admin = await signIn(browser, ADMIN);
  const examiner = await signIn(browser, EXAMINER);
  const aApi = admin.context.request;
  const eApi = examiner.context.request;
  const examId = await createExam(aApi);
  const me = await ok<{ id: string }>(await eApi.get("/api/proxy/me"), "examiner /me");
  expect((await aApi.post(`/api/proxy/exams/${examId}/assignments`, { data: { user_id: me.id } })).status()).toBe(204);
  const sid = await uploadBooklet(aApi, examId, "FIN-1");

  // --- the examiner maps and grades Q1 (partly) in the UI
  const ep = examiner.page;
  await ep.goto(`/submissions/${sid}`);
  await expect(ep.getByTestId("total")).toHaveText("0 / 5");
  await expect(ep.getByTestId("finalize-panel")).toHaveAttribute("data-state", "OPEN");
  await expect(ep.getByRole("button", { name: "Finalize result" })).toHaveCount(0); // an examiner cannot sign off
  await ep.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(ep, 0.05, 0.05, 0.9, 0.2);
  await expect(ep.getByText("Answer area saved.")).toBeVisible();
  await ep.getByRole("radio", { name: /Part/ }).check();
  await ep.getByRole("button", { name: "Save grade" }).click();
  await expect(ep.getByText("Grade saved.")).toBeVisible();
  await expect(ep.getByTestId("total")).toHaveText("1 / 5");

  // --- the administrator sees what is left to confirm, and finalizes
  const ap = admin.page;
  await ap.goto(`/submissions/${sid}`);
  const panel = ap.getByTestId("finalize-panel");
  await expect(panel).toContainText("Every answer that was mapped has a verdict for every criterion.");
  const finalize = ap.getByRole("button", { name: "Finalize result" });
  await expect(finalize).toBeDisabled(); // Q2 has no answer box: a person must confirm it counts as not attempted
  await ap.getByRole("checkbox", { name: /No answer box for/ }).check();
  await expect(finalize).toBeEnabled();
  await finalize.click();
  await expect(panel).toHaveAttribute("data-state", "FINALIZED");
  await expect(ap.getByTestId("final-total")).toHaveText("1 / 5");
  await expect(panel).toContainText("Snapshot 1");

  // --- read-only everywhere
  await ep.reload();
  await expect(ep.getByTestId("finalize-panel")).toHaveAttribute("data-state", "FINALIZED");
  await expect(ep.getByRole("radio", { name: /Full/ })).toBeDisabled();
  await expect(ep.getByRole("button", { name: "Save grade" })).toHaveCount(0);
  await expect(ep.getByRole("button", { name: "Draw answer box" })).toHaveCount(0);
  await expect(ep.getByRole("button", { name: "Reopen…" })).toHaveCount(0); // not for an examiner
  const region = (await workspace(eApi, sid)).regions[0]!;
  const blocked = await eApi.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: "full" } } });
  expect(blocked.status()).toBe(409);
  expect((await blocked.json()).error.code).toBe("finalized");
  expect((await eApi.delete(`/api/proxy/submissions/${sid}/regions/${region.id}`)).status()).toBe(409);
  expect((await eApi.post(`/api/proxy/submissions/${sid}/finalize`, { data: {} })).status()).toBe(403);
  expect((await eApi.post(`/api/proxy/submissions/${sid}/reopen`, { data: { reason: "I would like to change it" } })).status()).toBe(403);

  // --- the totals page says so
  await ap.goto(`/exams/${examId}/totals`);
  await expect(ap.getByText("Finalized (snapshot 1)")).toBeVisible();

  // --- reopen needs a reason
  await ap.goto(`/submissions/${sid}`);
  await ap.getByRole("button", { name: "Reopen…" }).click();
  const reopenBtn = ap.getByRole("button", { name: "Reopen for changes" });
  await ap.getByLabel(/Why is it being reopened/).fill("too short");
  await expect(reopenBtn).toBeDisabled();
  await ap.getByLabel(/Why is it being reopened/).fill("Q1 re-marked after moderation.");
  await reopenBtn.click();
  await expect(panel).toHaveAttribute("data-state", "OPEN");

  // --- the examiner changes the grade; the administrator finalizes again: snapshot 2
  await ep.reload();
  await ep.getByRole("radio", { name: /Full/ }).check();
  await ep.getByRole("button", { name: "Save grade" }).click();
  await expect(ep.getByText("Grade saved.")).toBeVisible();
  await ap.reload();
  await ap.getByRole("checkbox", { name: /No answer box for/ }).check();
  await ap.getByRole("button", { name: "Finalize result" }).click();
  await expect(panel).toContainText("Snapshot 2");
  await expect(ap.getByTestId("final-total")).toHaveText("2 / 5");
  await panel.getByText(/History/).click();
  const history = await ap.getByTestId("finalize-history").allInnerTexts();
  expect(history.map((h) => h.split("·")[0]!.trim())).toEqual(["Finalized", "Reopened", "Finalized"]);
  expect(history.join(" ")).toContain("Q1 re-marked after moderation.");

  // --- both snapshots are in the API and the audit trail has the sign-off record
  const snaps = await ok<{ snapshot_no: number; total: string }[]>(await aApi.get(`/api/proxy/submissions/${sid}/snapshots`), "snapshots");
  expect(snaps.map((s) => [s.snapshot_no, s.total])).toEqual([[1, "1"], [2, "2"]]);
  await ap.goto(`/exams/${examId}/audit`);
  const cells = ap.getByRole("table").locator("tbody tr td:nth-child(2)");
  await expect.poll(async () => (await cells.allInnerTexts()).includes("submission.reopen"), { timeout: 20_000 }).toBe(true);
  const actions = await cells.allInnerTexts();
  console.log(`::notice title=Finalize audit trail (oldest first)::${actions.join(" | ")}`);
  expect(actions.filter((a) => a === "submission.finalize")).toHaveLength(2);
  expect(actions.filter((a) => a === "submission.reopen")).toHaveLength(1);

  await admin.context.close();
  await examiner.context.close();
});
