import { expect, test, type Page } from "@playwright/test";
import { ADMIN, createExam, signIn, uploadBooklet } from "./helpers";

/**
 * 4.6: the Content-Security-Policy does its job and the app works under it.
 * (1) no page in the main flow causes a policy violation (page images from the object store included);
 * (2) markup injected into the page with an inline event handler or a javascript: link does NOT run, and the browser reports it.
 */
test.setTimeout(120_000);

async function watch(page: Page): Promise<string[]> {
  const violations: string[] = [];
  await page.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      console.log(`CSPVIOLATION ${e.violatedDirective} ${e.blockedURI}`);
    });
  });
  page.on("console", (m) => {
    if (m.text().startsWith("CSPVIOLATION") || /Content Security Policy|violates the following/i.test(m.text())) violations.push(m.text().slice(0, 200));
  });
  return violations;
}

test("no violations across the app, and an injected inline script is blocked", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const { context, page } = await signIn(browser, ADMIN);
  const examId = await createExam(context.request);
  const sid = await uploadBooklet(context.request, examId, "CSP-1");
  const violations = await watch(page);

  for (const path of ["/", "/exams", `/exams/${examId}/paper`, `/exams/${examId}/rubric`, `/exams/${examId}/submissions`, `/submissions/${sid}`]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle");
  }
  // the booklet page image really loaded (it comes from the object store's origin, which the policy names)
  const loaded = await page.getByRole("img", { name: "Booklet page 1" }).evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0);
  expect(loaded, "the page image was blocked or failed to load").toBe(true);
  expect(violations, `policy violations: ${violations.join(" | ")}`).toEqual([]);

  // an attacker's MARKUP: text that reaches the page as HTML (the way a stored-XSS bug would) with an inline event handler. The handler must not
  // run, and the browser must report that it blocked it. (A script that trusted page code creates itself is a different thing: 'strict-dynamic'
  // allows those by design, so that is not what is tested here.)
  const injected = await page.evaluate(async () => {
    const seen = new Promise<boolean>((resolve) => {
      document.addEventListener("securitypolicyviolation", () => resolve(true), { once: true });
      setTimeout(() => resolve(false), 2000);
    });
    const box = document.createElement("div");
    box.innerHTML = `<img src="x" onerror="window.__injected='ran'"><a href="javascript:window.__injected='ran'" id="evil">x</a>`;
    document.body.appendChild(box);
    (document.getElementById("evil") as HTMLAnchorElement).click();
    const blocked = await seen;
    return { ran: (window as unknown as { __injected?: string }).__injected === "ran", blocked };
  });
  expect(injected.ran, "injected inline event handler / javascript: URL ran: the policy is not effective").toBe(false);
  expect(injected.blocked, "the browser did not report the blocked handler").toBe(true);

  // and the headers are on every kind of response
  const res = await context.request.get("/exams");
  expect(res.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  await context.close();
});
