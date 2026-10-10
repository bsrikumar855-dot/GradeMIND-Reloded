import { defineConfig } from "@playwright/test";

/** Phase 2 end-to-end test: runs against a live docker compose stack (see .github/workflows/ci.yml, job compose-smoke). */
export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  expect: { timeout: 20_000 },
  retries: 0,
  workers: 1,
  // "github" turns failures into job annotations, which stay readable even where raw job logs are not.
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  outputDir: "test-results",
  use: {
    baseURL: process.env.E2E_WEB ?? "http://127.0.0.1:3100",
    // CI runners ship Google Chrome; using it avoids downloading a browser from a CDN.
    channel: process.env.E2E_CHANNEL ?? "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
