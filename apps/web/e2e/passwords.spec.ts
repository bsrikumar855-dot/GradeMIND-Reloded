import { expect, test, type Browser } from "@playwright/test";
import { ADMIN, ok, signIn } from "./helpers";

/**
 * 5.0 (D30.3): a person changes their own password, which signs out their other sessions; an administrator resets someone's password, and
 * that person can do nothing but choose a new one when they next sign in.
 */
test.setTimeout(120_000);

async function signInRaw(browser: Browser, email: string, password: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  return { context, page };
}

test("change your own password; other sessions end; then an admin reset forces a change", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const stamp = Date.now();
  const email = `pw.${stamp}@example.edu`;
  const first = `First-Pass-${stamp}-aa`;
  const second = `Second-Pass-${stamp}-bb`;
  const third = `Third-Pass-${stamp}-cc`;
  const admin = await signIn(browser, ADMIN);
  const created = await ok<{ id: string }>(
    await admin.context.request.post("/api/proxy/users", { data: { email, display_name: `Pw Person ${stamp}`, role: "examiner", password: first, must_change_password: false } }),
    "create",
  );

  // --- two devices signed in
  const laptop = await signIn(browser, { email, password: first });
  const phone = await signIn(browser, { email, password: first });
  expect((await phone.context.request.get("/api/proxy/me")).status()).toBe(200);

  // --- the laptop changes the password (refusals first)
  const p = laptop.page;
  await p.getByRole("link", { name: "Account" }).click();
  await expect(p).toHaveURL(/\/account\/password$/);
  const submit = async (cur: string, next: string, again = next) => {
    await p.getByLabel("Current password").fill(cur);
    await p.getByLabel("New password", { exact: true }).fill(next);
    await p.getByLabel("New password again").fill(again);
    await p.getByRole("button", { name: "Change password" }).click();
  };
  await submit("not-my-password-1", second);
  await expect(p.getByText("The current password is not correct.")).toBeVisible();
  await submit(first, second, `${second}x`);
  await expect(p.getByText("The two new passwords are not the same.")).toBeVisible();
  await submit(first, first);
  await expect(p.getByText(/That password is not accepted/)).toBeVisible();
  await submit(first, second);
  await expect(p.getByTestId("password-changed")).toBeVisible();
  expect((await laptop.context.request.get("/api/proxy/me")).status()).toBe(200); // this session was given a fresh token
  expect((await phone.context.request.get("/api/proxy/me")).status()).toBe(401); // the other device is signed out
  await phone.page.goto("/exams");
  await expect(phone.page).toHaveURL(/\/login/);

  // --- the old password no longer works, the new one does
  const old = await signInRaw(browser, email, first);
  await expect(old.page.getByText("The email or password is not correct.")).toBeVisible();
  await old.context.close();
  const fresh = await signIn(browser, { email, password: second });

  // --- an administrator resets it (UI), and shows the temporary password once
  await admin.page.goto("/admin/users");
  await admin.page.getByRole("button", { name: `Reset password of Pw Person ${stamp}` }).click();
  const temp = (await admin.page.getByTestId("temporary-password").innerText()).trim();
  expect(temp.length).toBeGreaterThanOrEqual(16);
  expect((await fresh.context.request.get("/api/proxy/me")).status()).toBe(401); // the reset ended their session
  await admin.page.getByRole("button", { name: `Deactivate Pw Person ${stamp}` }).waitFor(); // (the row is still there; nothing was deactivated)
  expect(created.id).toBeTruthy();

  // --- signing in with the temporary password leads to the change page and nowhere else
  const forced = await signInRaw(browser, email, temp);
  await expect(forced.page).toHaveURL(/\/account\/password\?required=1/);
  await expect(forced.page.getByText("An administrator gave you a temporary password")).toBeVisible();
  await forced.page.goto("/exams");
  await expect(forced.page).toHaveURL(/\/account\/password\?required=1/);
  expect((await forced.context.request.get("/api/proxy/exams")).status()).toBe(403);
  await forced.page.getByLabel("Temporary password").fill(temp);
  await forced.page.getByLabel("New password", { exact: true }).fill(third);
  await forced.page.getByLabel("New password again").fill(third);
  await forced.page.getByRole("button", { name: "Change password" }).click();
  await expect(forced.page.getByTestId("password-changed")).toBeVisible();
  await forced.page.goto("/exams");
  await expect(forced.page).toHaveURL(/\/exams$/); // free to work with their own password
  expect((await forced.context.request.get("/api/proxy/exams")).status()).toBe(200);

  for (const x of [admin, laptop, phone, fresh, forced]) await x.context.close();
});
