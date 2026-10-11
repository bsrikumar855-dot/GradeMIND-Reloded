import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { ADMIN, ok, signIn } from "./helpers";

/** 5.7: after the encrypted backup was restored OVER the live data, the system is used again over HTTPS: the administrator signs in, the
 *  Tamil-named exam is there, and its result sheet is produced again from the restored snapshot (saved for the PDF reader step). */
test.setTimeout(120_000);
test.skip(!process.env.E2E_RC_OUT, "needs E2E_RC_OUT: it runs in the release-candidate CI job, after the restore");

test("after the restore: sign in over HTTPS, the exam and its finalized result are back, the sheet is made again", async ({ browser }) => {
  const OUT = process.env.E2E_RC_OUT ?? "";
  expect(OUT, "E2E_RC_OUT must name the directory the first spec wrote to").toBeTruthy();
  const state = JSON.parse(fs.readFileSync(path.join(OUT, "state.json"), "utf8")) as { examId: string; sid: string; examName: string };
  const admin = await signIn(browser, ADMIN);
  expect(new URL(admin.page.url()).protocol).toBe("https:");
  const exams = await ok<{ id: string; name: string }[]>(await admin.context.request.get("/api/proxy/exams?limit=500"), "exams");
  expect(exams.find((e) => e.id === state.examId)?.name).toBe(state.examName);
  const sheet = await admin.context.request.get(`/api/proxy/submissions/${state.sid}/report.pdf`);
  expect(sheet.status()).toBe(200);
  fs.writeFileSync(path.join(OUT, "sheet-after-restore.pdf"), await sheet.body());
});
