import { expect, test, type Page } from "@playwright/test";
import { ADMIN, createExam, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.5: the whole grading flow with the KEYBOARD ONLY: sign in, open the exam and the booklet, draw an answer box, grade it, finalize
 * (confirming the unanswered question), download link reachable, reopen with a reason. No pointer is used: the page records any
 * real mouse or touch press and the test fails if there was one. Setup (the exam, the paper, the rubric, the uploaded booklet) goes
 * through the API; everything the examiner does goes through Tab, Shift+Tab, Enter, Space, arrows, digits and typing.
 */
test.setTimeout(240_000);

/** The accessible name of whatever has focus, in the form tests can match: label, aria-label, text or value. */
async function focusName(page: Page): Promise<string> {
  return page.evaluate(() => {
    const el = document.activeElement as (HTMLElement & { labels?: NodeListOf<HTMLLabelElement>; value?: string }) | null;
    if (!el || el === document.body) return "";
    const label = el.labels?.[0]?.innerText ?? "";
    return (el.getAttribute("aria-label") || label || el.innerText || el.getAttribute("name") || "").trim().replace(/\s+/g, " ");
  });
}

/** Tab forward until the focused control's name matches; fails with the trail of what was passed over. */
async function tabTo(page: Page, name: RegExp | string, max = 150): Promise<void> {
  const seen: string[] = [];
  for (let i = 0; i < max; i++) {
    await page.keyboard.press("Tab");
    const n = await focusName(page);
    seen.push(n);
    if (typeof name === "string" ? n === name : name.test(n)) return;
  }
  throw new Error(`keyboard focus never reached ${String(name)} in ${max} Tab presses; passed: ${seen.filter(Boolean).slice(-25).join(" | ")}`);
}

test("grading from sign-in to finalize and reopen, without a pointer", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const stamp = Date.now();
  const name = `Keyboard ${stamp}`;

  // setup through the API, in a separate signed-in context (the keyboard run below starts signed out)
  const setup = await signIn(browser, ADMIN);
  const examId = await createExam(setup.context.request, name);
  const sid = await uploadBooklet(setup.context.request, examId, "KB-FLOW");
  await setup.context.close();

  const context = await browser.newContext();
  const page = await context.newPage();
  await page.addInitScript(() => {
    (window as unknown as { __pointer: number }).__pointer = 0;
    for (const t of ["mousedown", "pointerdown", "touchstart"]) {
      window.addEventListener(t, (e) => { if (e.isTrusted && (e as PointerEvent).pointerType !== "") (window as unknown as { __pointer: number }).__pointer++; }, true);
    }
  });

  // --- sign in with the keyboard
  await page.goto("/login");
  await tabTo(page, /^Email$/);
  await page.keyboard.type(ADMIN.email);
  await page.keyboard.press("Tab");
  expect(await focusName(page)).toBe("Password");
  await page.keyboard.type(ADMIN.password);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/$/);

  // --- exam list -> exam -> booklets -> grade
  await tabTo(page, "Exams");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/exams$/);
  // the exam list is oldest first and has no paging: on a database that has seen many runs the new exam is far down the Tab order
  await tabTo(page, name, 1500);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/exams/${examId}/paper`));
  await tabTo(page, "Booklets");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/exams/${examId}/submissions`));
  await tabTo(page, "Grade");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/submissions/${sid}`));
  await expect(page.getByTestId("total")).toHaveText("0 / 5");

  // --- draw an answer box: the button, then Enter to place, Enter to confirm
  await tabTo(page, "Draw answer box");
  await page.keyboard.press("Space");
  await expect(page.getByTestId("page-canvas")).toBeFocused(); // focus moved to the page, ready for the box
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("kb-box")).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("Shift+ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  expect((await workspace(context.request, sid)).regions).toHaveLength(1);

  // --- grade it with digits and Ctrl+Enter (focus is on the page, not in a text box)
  await page.keyboard.press("3"); // the third level of the first (only) criterion: Full
  await page.keyboard.press("Control+Enter");
  await expect(page.getByText("Grade saved.")).toBeVisible();
  await expect(page.getByTestId("total")).toHaveText("2 / 5");

  // --- finalize: the unanswered question must be confirmed, with the keyboard, before the button works
  await tabTo(page, /No answer box for/);
  await page.keyboard.press("Space");
  await expect(page.getByRole("checkbox", { name: /No answer box for/ })).toBeChecked();
  await page.keyboard.press("Shift+Tab"); // back out of the checkbox, then forward to the button: order is checkbox -> Finalize
  await page.keyboard.press("Tab");
  await tabTo(page, "Finalize result", 5);
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("finalize-panel")).toHaveAttribute("data-state", "FINALIZED");
  await expect(page.getByTestId("final-total")).toHaveText("2 / 5");

  // --- the result sheet link is reachable, then reopen with a reason
  await tabTo(page, /Download result sheet/);
  await tabTo(page, /^Reopen/);
  await page.keyboard.press("Enter");
  await tabTo(page, /Why is it being reopened/);
  await page.keyboard.type("Keyboard-only check finished.");
  await tabTo(page, "Reopen for changes");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("finalize-panel")).toHaveAttribute("data-state", "OPEN");

  // --- and nothing in all of that was a pointer
  expect(await page.evaluate(() => (window as unknown as { __pointer: number }).__pointer)).toBe(0);
  await context.close();
});
