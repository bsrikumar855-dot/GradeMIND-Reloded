import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { ADMIN, createExam, drawBox, ok, question, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.7: the Phase 4 pilot flow end to end, with nothing mocked. Two examiners and a teacher, two booklets:
 * examiners grade in the UI, the second examiner OVERRIDES the first on a shared answer (with a reason), the teacher FINALIZES both
 * booklets, downloads the result sheet and the summary, REOPENS one with a reason, the first examiner changes a grade, the teacher
 * finalizes again (snapshot 2), and the sheet then names snapshot 2. The CI steps that follow run `verify-snapshots` and the backup/restore
 * drill on exactly the data this test leaves behind, and `scripts/phase4_evidence.sh` prints it from the database.
 */
test.setTimeout(480_000);

type Who = { context: BrowserContext; page: Page; email: string; id: string };
const PW = () => `P4-${crypto.randomUUID()}-x`; // 12+ characters, never logged

async function signedIn(browser: Parameters<typeof signIn>[0], email: string, password: string, userId: string): Promise<Who> {
  const { context, page } = await signIn(browser, { email, password });
  return { context, page, email, id: userId };
}

test("two examiners and a teacher: grade, override, finalize, report, reopen, finalize again", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const stamp = Date.now();
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;

  // --- the people: created by the administrator through the API (the UI for it is covered by admin-users.spec.ts)
  const make = async (role: "examiner" | "teacher", name: string) => {
    const password = PW();
    const email = `p4.${name}.${stamp}@example.edu`;
    const u = await ok<{ id: string }>(await api.post("/api/proxy/users", { data: { email, display_name: `P4 ${name}`, role, password } }), `create ${name}`);
    return { email, password, id: u.id };
  };
  const [a, b, t] = [await make("examiner", "alice"), await make("examiner", "bob"), await make("teacher", "tara")];
  const examId = await createExam(api, `Phase 4 ${stamp}`);
  for (const u of [a, b]) expect((await api.post(`/api/proxy/exams/${examId}/assignments`, { data: { user_id: u.id } })).status()).toBe(204);
  const s1 = await uploadBooklet(api, examId, `P4-A-${stamp}`);
  const s2 = await uploadBooklet(api, examId, `P4-B-${stamp}`);

  const alice = await signedIn(browser, a.email, a.password, a.id);
  const bob = await signedIn(browser, b.email, b.password, b.id);
  const tara = await signedIn(browser, t.email, t.password, t.id);

  // --- Alice grades booklet 1: Q1 Full (2), Q2 Full (3)
  const ap = alice.page;
  await ap.goto(`/submissions/${s1}`);
  await question(ap, 1).click();
  await ap.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(ap, 0.1, 0.1, 0.8, 0.3);
  await expect(ap.getByText("Answer area saved.")).toBeVisible();
  await ap.getByRole("radio", { name: /Full/ }).check();
  await ap.getByRole("button", { name: "Save grade" }).click();
  await expect(ap.getByText("Grade saved.")).toBeVisible();
  await question(ap, 2).click();
  await drawBox(ap, 0.1, 0.4, 0.8, 0.6);
  await expect(ap.getByText("Answer area saved.")).toBeVisible();
  await ap.getByRole("radio", { name: /Full/ }).check();
  await ap.getByRole("button", { name: "Save grade" }).click();
  await expect(ap.getByTestId("total")).toHaveText("5 / 5");

  // --- Bob grades booklet 2: Q1 Part (1), Q2 None (0)
  const bp = bob.page;
  await bp.goto(`/submissions/${s2}`);
  await question(bp, 1).click();
  await bp.getByRole("button", { name: "Draw answer box" }).click();
  await drawBox(bp, 0.1, 0.1, 0.8, 0.3);
  await expect(bp.getByText("Answer area saved.")).toBeVisible();
  await bp.getByRole("radio", { name: /Part/ }).check();
  await bp.getByRole("button", { name: "Save grade" }).click();
  await expect(bp.getByText("Grade saved.")).toBeVisible();
  await question(bp, 2).click();
  await drawBox(bp, 0.1, 0.4, 0.8, 0.6);
  await expect(bp.getByText("Answer area saved.")).toBeVisible();
  await bp.getByRole("radio", { name: /None/ }).check();
  await bp.getByRole("button", { name: "Save grade" }).click();
  await expect(bp.getByTestId("total")).toHaveText("1 / 5");

  // --- Bob overrides Alice's Q1 on booklet 1: it is refused without a reason, accepted with one
  await bp.goto(`/submissions/${s1}`);
  await question(bp, 1).click();
  await bp.getByRole("radio", { name: /Part/ }).check();
  await bp.getByRole("button", { name: "Save grade" }).click();
  await expect(bp.getByText(/Another examiner graded this answer/)).toBeVisible();
  await bp.getByLabel(/Reason for changing another examiner/).fill("The definition leaves out the non-living part.");
  await bp.getByRole("button", { name: "Save grade" }).click();
  await expect(bp.getByText("Grade saved.")).toBeVisible();
  await expect(bp.getByTestId("total")).toHaveText("4 / 5");

  // --- the examiners cannot sign off; the teacher can, for both booklets
  expect((await alice.context.request.post(`/api/proxy/submissions/${s1}/finalize`, { data: {} })).status()).toBe(403);
  expect((await tara.context.request.post("/api/proxy/users", { data: { email: `x.${stamp}@example.edu`, display_name: "X", role: "admin", password: PW() } })).status()).toBe(403);
  const tp = tara.page;
  for (const [sid, total] of [[s1, "4 / 5"], [s2, "1 / 5"]] as const) {
    await tp.goto(`/submissions/${sid}`);
    await expect(tp.getByTestId("finalize-panel")).toHaveAttribute("data-state", "OPEN");
    await tp.getByRole("button", { name: "Finalize result" }).click();
    await expect(tp.getByTestId("finalize-panel")).toHaveAttribute("data-state", "FINALIZED");
    await expect(tp.getByTestId("final-total")).toHaveText(total);
  }
  // finalized = read-only for everyone, examiners included
  expect((await alice.context.request.put(`/api/proxy/submissions/${s1}/evaluations/q2/1`, { data: { verdicts: { c1: "none" } } })).status()).toBe(409);

  // --- reports: the result sheet and the summary name the snapshots they come from
  const snaps1 = await ok<{ id: string; snapshot_no: number; total: string }[]>(await tara.context.request.get(`/api/proxy/submissions/${s1}/snapshots`), "snapshots");
  expect(snaps1.map((s) => [s.snapshot_no, s.total])).toEqual([[1, "4"]]);
  const sheet1 = await tara.context.request.get(`/api/proxy/submissions/${s1}/report.pdf`);
  expect(sheet1.status()).toBe(200);
  expect((await sheet1.body()).toString("latin1")).toContain(snaps1[0]!.id);
  const csv1 = await (await tara.context.request.get(`/api/proxy/exams/${examId}/summary.csv`)).text();
  expect(csv1.split("\n").filter((l) => l.includes(`P4-`)).length).toBe(2);
  expect(csv1).toContain(snaps1[0]!.id);

  // --- analytics: two booklets is too few for a mean (the n < 5 rule), and the override is counted
  const an = await ok<{ questions: { qid: string; suppressed: boolean; mean: string | null }[]; overrides: { overrides: number; grades_saved: number } }>(
    await tara.context.request.get(`/api/proxy/exams/${examId}/analytics`),
    "analytics",
  );
  expect(an.questions.every((q) => q.suppressed && q.mean === null)).toBe(true);
  expect(an.overrides.overrides).toBe(1);

  // --- the teacher reopens booklet 1 (a reason is required); Alice changes Q2; the teacher finalizes again
  await tp.goto(`/submissions/${s1}`);
  await tp.getByRole("button", { name: "Reopen…" }).click();
  await tp.getByLabel(/Why is it being reopened/).fill("Q2 re-marked after the moderation meeting.");
  await tp.getByRole("button", { name: "Reopen for changes" }).click();
  await expect(tp.getByTestId("finalize-panel")).toHaveAttribute("data-state", "OPEN");
  expect((await tara.context.request.get(`/api/proxy/submissions/${s1}/report.pdf`)).status()).toBe(409); // no sheet from stale numbers
  await ap.goto(`/submissions/${s1}`);
  await question(ap, 2).click();
  await ap.getByRole("radio", { name: /None/ }).check();
  await ap.getByRole("button", { name: "Save grade" }).click();
  await expect(ap.getByText("Grade saved.")).toBeVisible();
  await expect(ap.getByTestId("total")).toHaveText("1 / 5");
  await tp.reload();
  await tp.getByRole("button", { name: "Finalize result" }).click();
  await expect(tp.getByTestId("finalize-panel")).toContainText("Snapshot 2");
  await expect(tp.getByTestId("final-total")).toHaveText("1 / 5");

  // --- the new sheet names snapshot 2 and not snapshot 1; the old snapshot is untouched
  const snaps = await ok<{ id: string; snapshot_no: number; total: string }[]>(await tara.context.request.get(`/api/proxy/submissions/${s1}/snapshots`), "snapshots");
  expect(snaps.map((s) => [s.snapshot_no, s.total])).toEqual([[1, "4"], [2, "1"]]);
  const sheet2 = (await (await tara.context.request.get(`/api/proxy/submissions/${s1}/report.pdf`)).body()).toString("latin1");
  expect(sheet2).toContain(snaps[1]!.id);
  expect(sheet2).not.toContain(snaps[0]!.id);
  await tara.context.request.get(`/api/proxy/exams/${examId}/summary.pdf`); // generated and audited

  // --- the audit trail of the whole exam, oldest first (also printed as CI evidence)
  await tp.goto(`/exams/${examId}/audit`);
  const cells = tp.getByRole("table").locator("tbody tr td:nth-child(2)");
  await expect.poll(async () => (await cells.allInnerTexts()).includes("report.summary_pdf"), { timeout: 20_000 }).toBe(true);
  const actions = await cells.allInnerTexts();
  console.log(`::notice title=Phase 4 audit trail (oldest first)::${actions.join(" | ")}`);
  const count = (x: string) => actions.filter((v) => v === x).length;
  expect(count("evaluation.override")).toBe(1);
  expect(count("submission.finalize")).toBe(3);
  expect(count("submission.reopen")).toBe(1);
  expect(count("report.student_pdf")).toBeGreaterThanOrEqual(2);
  expect(count("report.summary_csv")).toBeGreaterThanOrEqual(1);
  expect(count("exam.assign")).toBe(2);
  const w = await workspace(api, s1);
  console.log(`::notice title=Phase 4 final state::booklet 1: ${snaps.length} snapshots (totals ${snaps.map((s) => s.total).join(" then ")}), score ${w.score.total}`);

  for (const x of [alice, bob, tara, admin]) await x.context.close();
});
