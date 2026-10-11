import { expect, test } from "@playwright/test";
import { ADMIN, createExam, signIn, uploadBooklet } from "./helpers";

/**
 * 5.3 (D30.5): the stack behind the HTTPS reverse proxy. Run only against the https profile (E2E_HTTPS=1). Checks what the browser actually sees: the
 * session cookie's flags, HSTS and the CSP, that booklet page images load from the HTTPS object-store origin with no mixed-content or policy
 * violation, and that plain HTTP is not served.
 */
test.skip(process.env.E2E_HTTPS !== "1", "needs the https compose profile (E2E_HTTPS=1)");
test.setTimeout(180_000);
const STORAGE = process.env.E2E_STORAGE_ORIGIN ?? "https://localhost:9443";

test("over HTTPS: secure cookie, HSTS, CSP, images from the HTTPS object store, no plain HTTP", async ({ browser, baseURL }) => {
  expect(baseURL, "E2E_WEB must be the https URL").toMatch(/^https:\/\//);
  const { context, page } = await signIn(browser, ADMIN);
  const problems: string[] = [];
  page.on("console", (m) => {
    if (/mixed content|content security policy|violates the following/i.test(m.text())) problems.push(m.text().slice(0, 200));
  });

  // the session cookie
  const cookie = (await context.cookies()).find((c) => c.name === "gm_session");
  expect(cookie, "no session cookie").toBeTruthy();
  expect(cookie!.secure, "the session cookie must be Secure").toBe(true);
  expect(cookie!.httpOnly).toBe(true);
  expect(cookie!.sameSite).toBe("Lax");

  // headers the browser got on a page
  const res = await page.goto("/exams");
  const h = res!.headers();
  expect(h["strict-transport-security"]).toMatch(/max-age=\d+/);
  expect(h["content-security-policy"]).toContain(`img-src 'self' blob: data: ${STORAGE}`);
  expect(h["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(h["server"], "the proxy must not announce itself").toBeUndefined();
  expect(await page.evaluate(() => [location.protocol, window.isSecureContext])).toEqual(["https:", true]);

  // a booklet page image comes from the HTTPS object store and loads
  const examId = await createExam(context.request);
  const sid = await uploadBooklet(context.request, examId, `HTTPS-${Date.now()}`);
  await page.goto(`/submissions/${sid}`);
  const img = page.getByRole("img", { name: "Booklet page 1" });
  await expect(img).toBeVisible();
  expect(await img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0), "the page image did not load").toBe(true);
  expect(await img.getAttribute("src")).toMatch(new RegExp(`^${STORAGE.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}/`));
  expect(problems, `mixed content or policy violations: ${problems.join(" | ")}`).toEqual([]);

  // plain HTTP on the same port is not an HTTP site
  const plain = await context.request.get(baseURL!.replace("https://", "http://") + "/login", { timeout: 8000 }).then(
    (r) => `served ${r.status()}`,
    () => "refused",
  );
  expect(plain).not.toMatch(/^served 2/);
  await context.close();
});
