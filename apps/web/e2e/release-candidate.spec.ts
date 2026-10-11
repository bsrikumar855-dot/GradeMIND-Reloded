import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { ADMIN, createExam, ok, signIn, uploadBooklet } from "./helpers";

/**
 * 5.7: the release-candidate run over HTTPS on a FRESH stack: an examiner signs in with a temporary password and is made to choose their own,
 * the administrator finalizes a booklet of an exam whose name and subject are in Tamil and Devanagari, and the result sheet and the exam summary
 * are saved for the CI step that reads them back with a PDF reader (no "?" where a letter should be). Passwords, the reset flow and the rubric
 * guard run in their own specs in the same job (passwords.spec.ts, rubric-guard.spec.ts); the CI steps after this run the evidence script,
 * `verify-snapshots`, the encrypted backup and the restore, and `rc-after-restore.spec.ts` checks the restored system over HTTPS.
 */
test.setTimeout(300_000);

const OUT = process.env.E2E_RC_OUT ?? "";
test.skip(!OUT, "needs E2E_RC_OUT: it runs in the release-candidate CI job (fresh stack over HTTPS), not in the plain stack run");
const TAMIL_NAME = "தமிழ் தேர்வு";
const DEVANAGARI_SUBJECT = "हिन्दी विषय";

test("fresh stack over HTTPS: a Tamil/Devanagari exam is graded, finalized and its sheet and summary are produced", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  expect(OUT, "E2E_RC_OUT must name a directory for the saved PDFs").toBeTruthy();
  fs.mkdirSync(OUT, { recursive: true });
  const stamp = Date.now();
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;
  expect(new URL(admin.page.url()).protocol).toBe("https:");

  // --- the cookie that carries the session is Secure and HttpOnly (this stack runs behind the HTTPS proxy)
  const cookies = await admin.context.cookies();
  expect(cookies.length).toBeGreaterThan(0);
  for (const c of cookies) expect(c.secure && c.httpOnly, `cookie ${c.name} is Secure and HttpOnly`).toBe(true);

  // --- an examiner is created, the administrator resets their password, and the temporary one gets them nowhere but the change page
  const examinerEmail = `rc.examiner.${stamp}@example.edu`;
  const chosen = `Chosen-${crypto.randomUUID()}-y`;
  const created = await ok<{ id: string }>(
    await api.post("/api/proxy/users", { data: { email: examinerEmail, display_name: "RC Examiner", role: "examiner", password: `First-${crypto.randomUUID()}-x` } }),
    "create examiner",
  );
  const { temporary_password: temp } = await ok<{ temporary_password: string }>(await api.post(`/api/proxy/users/${created.id}/reset-password`), "reset");
  expect(temp.length).toBeGreaterThanOrEqual(16);
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(examinerEmail);
  await page.getByLabel("Password").fill(temp);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/account\/password\?required=1/);
  expect((await ctx.request.get("/api/proxy/exams")).status()).toBe(403); // nothing else is open to them yet

  // --- the exam: Tamil name, Devanagari subject; one booklet, graded and finalized by the administrator
  const examId = await createExam(api, `${TAMIL_NAME} ${stamp}`, DEVANAGARI_SUBJECT);
  const sid = await uploadBooklet(api, examId, `RC-${stamp}`);
  const ws = await ok<{ pages: { id: string }[] }>(await api.get(`/api/proxy/submissions/${sid}/workspace`), "workspace");
  await ok(await api.post(`/api/proxy/submissions/${sid}/regions`, { data: { page_id: ws.pages[0]!.id, bbox: [0.1, 0.1, 0.9, 0.3], qid: "q1" } }), "region");
  await ok(await api.put(`/api/proxy/submissions/${sid}/evaluations/q1/1`, { data: { verdicts: { c1: "full" } } }), "grade");
  const fin = await api.post(`/api/proxy/submissions/${sid}/finalize`, { data: { confirm_not_attempted: true } });
  expect(fin.status(), await fin.text()).toBe(201);

  // --- the result sheet and the summary: real PDFs, named in plain ASCII even though the exam name is not
  const sheet = await api.get(`/api/proxy/submissions/${sid}/report.pdf`);
  expect(sheet.status()).toBe(200);
  expect(sheet.headers()["content-type"]).toBe("application/pdf");
  fs.writeFileSync(path.join(OUT, "sheet.pdf"), await sheet.body());
  const summary = await api.get(`/api/proxy/exams/${examId}/summary.pdf`);
  expect(summary.status()).toBe(200);
  const disposition = summary.headers()["content-disposition"] ?? "";
  expect(disposition).toMatch(/^attachment; filename="[\x20-\x7e]+"; filename\*=UTF-8''/); // every header byte is ASCII
  fs.writeFileSync(path.join(OUT, "summary.pdf"), await summary.body());

  // --- what the CI steps after this need to know
  fs.writeFileSync(path.join(OUT, "state.json"), JSON.stringify({ examId, sid, stamp, examName: `${TAMIL_NAME} ${stamp}`, subject: DEVANAGARI_SUBJECT, student: `RC-${stamp}` }));

  // --- and the examiner finally chooses their own password (kept last: it is the part that ends their temporary session)
  await page.getByLabel("Temporary password").fill(temp);
  await page.getByLabel("New password", { exact: true }).fill(chosen);
  await page.getByLabel("New password again").fill(chosen);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByTestId("password-changed")).toBeVisible();
  await ctx.close();
});
