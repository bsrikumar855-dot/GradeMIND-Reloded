import { expect, test } from "@playwright/test";
import { ADMIN, ok, signIn } from "./helpers";

/**
 * 4.1: user administration through the UI and what an examiner then can and cannot reach.
 * An administrator creates an examiner (with a generated temporary password), assigns them to one of two exams, and the examiner
 * signs in: they see that exam only, no Users, no Examiners tab, no Audit trail; the API refuses what the UI hides. Then the
 * administrator removes the assignment and finally deactivates the account, and the examiner's access ends at once.
 */
test.setTimeout(120_000);

test("admin creates an examiner and assigns them; the examiner sees only their exam and no admin pages", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const stamp = Date.now();
  const email = `dana.${stamp}@example.edu`;
  const name = `Dana Examiner ${stamp}`;
  const admin = await signIn(browser, ADMIN);
  const api = admin.context.request;
  const page = admin.page;

  // two exams: one will be assigned, one never
  const mk = async (n: string) => (await ok<{ id: string }>(await api.post("/api/proxy/exams", { data: { name: n, subject: "Biology", total_marks: "5" } }), "create exam")).id;
  const assigned = await mk(`Assigned ${stamp}`);
  const hidden = await mk(`Hidden ${stamp}`);

  // --- create the user in the UI
  await page.getByRole("link", { name: "Users" }).click();
  await expect(page).toHaveURL(/\/admin\/users$/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Role").selectOption("examiner");
  await page.getByRole("button", { name: "Generate" }).click();
  const password = await page.getByLabel("Temporary password").inputValue();
  expect(password.length).toBeGreaterThanOrEqual(12);
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByText("Account created.")).toBeVisible();
  const row = page.getByTestId("user-row").filter({ hasText: email });
  await expect(row).toContainText("Examiner");
  await expect(row).toContainText("Active");
  await expect(page.getByTestId("user-row").filter({ hasText: ADMIN.email })).toContainText("This is you"); // no self-deactivation button

  // a second account with the same address is refused with a readable message
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Name").fill("Someone else");
  await page.getByLabel("Temporary password").fill("another-long-password-1");
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByText("already exists")).toBeVisible();

  // --- assign them to one exam in the UI
  await page.goto(`/exams/${assigned}/examiners`);
  await page.getByLabel("Examiner", { exact: true }).selectOption({ label: `${name} (${email})` });
  await page.getByRole("button", { name: "Assign" }).click();
  const assignedRow = page.getByTestId("assigned-row").filter({ hasText: email });
  await expect(assignedRow).toBeVisible();

  // --- the examiner signs in
  const ex = await signIn(browser, { email, password });
  const exApi = ex.context.request;
  const exPage = ex.page;
  await exPage.goto("/exams");
  await expect(exPage.getByRole("link", { name: `Assigned ${stamp}` })).toBeVisible();
  await expect(exPage.getByText(`Hidden ${stamp}`)).toHaveCount(0);
  await expect(exPage.getByRole("link", { name: "Users" })).toHaveCount(0); // no admin navigation
  await exPage.goto("/admin/users");
  await expect(exPage).toHaveURL(/\/$/); // the page sends them home

  await exPage.goto(`/exams/${assigned}/paper`);
  const tabs = exPage.getByRole("navigation", { name: "Exam sections" });
  await expect(tabs.getByRole("link", { name: "Booklets" })).toBeVisible();
  await expect(tabs.getByRole("link", { name: "Audit trail" })).toHaveCount(0);
  await expect(tabs.getByRole("link", { name: "Examiners" })).toHaveCount(0);
  await exPage.goto(`/exams/${assigned}/audit`);
  await expect(exPage).toHaveURL(new RegExp(`/exams/${assigned}/submissions$`)); // redirected away from the audit trail
  await exPage.goto(`/exams/${assigned}/examiners`);
  await expect(exPage).toHaveURL(new RegExp(`/exams/${assigned}/submissions$`));

  // the API refuses what the UI hides (the UI is not the access control)
  expect((await exApi.get(`/api/proxy/exams/${hidden}`)).status()).toBe(404);
  expect((await exApi.get(`/api/proxy/exams/${hidden}/submissions`)).status()).toBe(404);
  expect((await exApi.get(`/api/proxy/exams/${assigned}/audit`)).status()).toBe(403);
  expect((await exApi.get("/api/proxy/users")).status()).toBe(403);
  expect((await exApi.get(`/api/proxy/exams/${assigned}/assignments`)).status()).toBe(403);
  expect((await exApi.post("/api/proxy/users", { data: { email: `x.${stamp}@example.edu`, display_name: "X", role: "admin", password: "long-enough-password" } })).status()).toBe(403);

  // --- removing the assignment ends their access to the exam at once
  await page.reload();
  await assignedRow.getByRole("button", { name: `Remove ${name}` }).click();
  await expect(page.getByTestId("assigned-row")).toHaveCount(0);
  expect((await exApi.get(`/api/proxy/exams/${assigned}`)).status()).toBe(404);

  // --- deactivating the account ends the session they already hold
  expect((await exApi.get("/api/proxy/me")).status()).toBe(200);
  await page.goto("/admin/users");
  await page.getByRole("button", { name: `Deactivate ${name}` }).click();
  await expect(page.getByTestId("user-row").filter({ hasText: email })).toContainText("Deactivated");
  expect((await exApi.get("/api/proxy/me")).status()).toBe(401);
  await page.getByRole("button", { name: `Reactivate ${name}` }).click();
  await expect(page.getByTestId("user-row").filter({ hasText: email })).toContainText("Active");
  expect((await exApi.get("/api/proxy/me")).status()).toBe(200);

  await ex.context.close();
  await admin.context.close();
});
