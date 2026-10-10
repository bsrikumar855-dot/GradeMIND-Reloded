import { expect, test } from "@playwright/test";
import { ADMIN, createExam, signIn, uploadBooklet, workspace } from "./helpers";

/**
 * 4.5: drawing an answer box with a finger. The touches are real touch events sent through the browser's debugging protocol (Chrome turns
 * them into pointer events with pointerType "touch", which is what the page listens to). Taps are NOT driven through the touch emulation
 * (it proved unreliable for clicks in this browser), so the toggle buttons are clicked. Also: a finger drag that is NOT drawing must scroll
 * the page, not draw a box, and the page's controls are at least 44 px tall on a touch screen.
 */
test.setTimeout(90_000);

test("draw an answer box with a finger; a swipe without drawing on creates nothing", async ({ browser }) => {
  expect(ADMIN.email && ADMIN.password, "E2E_EMAIL and E2E_PASSWORD must be set").toBeTruthy();
  const setup = await signIn(browser, ADMIN);
  const examId = await createExam(setup.context.request);
  const sid = await uploadBooklet(setup.context.request, examId, "TOUCH-1");
  await setup.context.close();

  const context = await browser.newContext({ viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true });
  const page = await context.newPage();
  await page.goto("/login");
  await page.getByLabel("Email").fill(ADMIN.email);
  await page.getByLabel("Password").fill(ADMIN.password);
  await page.getByRole("button", { name: "Sign in" }).click(); // sign-in itself is not under test here
  await expect(page).toHaveURL(/\/$/);
  await page.goto(`/submissions/${sid}`);
  await expect(page.getByTestId("total")).toHaveText("0 / 5");
  const cdp = await context.newCDPSession(page);

  async function swipe(from: [number, number], to: [number, number], steps = 8): Promise<void> {
    await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: from[0], y: from[1] }] });
    for (let i = 1; i <= steps; i++) {
      const x = from[0] + ((to[0] - from[0]) * i) / steps;
      const y = from[1] + ((to[1] - from[1]) * i) / steps;
      await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y }] });
    }
    await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  }

  const canvas = page.getByTestId("page-canvas");
  await canvas.scrollIntoViewIfNeeded();
  const box = (await canvas.boundingBox())!;
  const at = (fx: number, fy: number): [number, number] => [box.x + box.width * fx, box.y + box.height * fy];

  // not drawing: a finger drag over the page must not create an answer box
  await swipe(at(0.2, 0.3), at(0.6, 0.45));
  expect((await workspace(context.request, sid)).regions).toHaveLength(0);

  // drawing on, with a tap on the button (a touch target)
  const draw = page.getByRole("button", { name: "Draw answer box" });
  expect((await draw.boundingBox())!.height).toBeGreaterThanOrEqual(43); // a finger-sized target on a touch screen
  await draw.click(); // switching drawing on is a plain click: only the finger drag below is under test
  await expect(page.getByTestId("draw-status")).toBeVisible();
  const box2 = (await canvas.boundingBox())!;
  const p0: [number, number] = [box2.x + box2.width * 0.1, box2.y + box2.height * 0.1];
  const p1: [number, number] = [box2.x + box2.width * 0.7, box2.y + box2.height * 0.3];
  await swipe(p0, p1);
  await expect(page.getByText("Answer area saved.")).toBeVisible();
  const regions = (await workspace(context.request, sid)).regions;
  expect(regions).toHaveLength(1);
  const [x0, y0, x1, y1] = regions[0]!.bbox as [number, number, number, number];
  expect(x0).toBeCloseTo(0.1, 1);
  expect(y0).toBeCloseTo(0.1, 1);
  expect(x1).toBeCloseTo(0.7, 1);
  expect(y1).toBeCloseTo(0.3, 1);

  // a stray tap while drawing (no drag) must not create a zero-size box
  await swipe(at(0.5, 0.6), at(0.5, 0.6), 1);
  await page.waitForTimeout(300);
  expect((await workspace(context.request, sid)).regions).toHaveLength(1);
  await context.close();
});
