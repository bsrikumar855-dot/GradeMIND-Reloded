# PHASE 5 REPORT: pilot release candidate (D30)

Status: **ready for owner review. Stopped at the phase gate; Phase 6, any cloud work and any model fine-tuning have not been started.**
Source of truth for test results is CI (rule 13). Last code commit: `88f56df`, CI run `38131070292`, **all five jobs green**:

| Job | Result | What it ran |
|---|---|---|
| `test` | success | `make test`: spike pytest 30 passed, 4 skipped (exit 0); app pytest **460 passed**, 100 % branch coverage gate kept on scoring, grading and OCR geometry (exit 0); `make lint` (ruff, mypy strict, 9 import contracts kept, single-path scanner 0 violations); alembic check |
| `web` | success | `tsc --noEmit`, `eslint --max-warnings 0`, 25 web unit tests (25 pass), `next build` |
| `secrets` | success | gitleaks over the full history |
| `compose-smoke` | success | the plain stack: smoke, **30 browser tests passed** (3 skipped: HTTPS-only and release-candidate-only specs), Phase 3 and Phase 4 evidence, `verify-snapshots` (8 snapshots), backup/restore drill (side copy and over the live data), encrypted-backup drill, OCR kill/restart, the HTTPS profile checks, the Trivy image scan (report) |
| `release-candidate` (new) | success | a fresh stack behind HTTPS from its first start; see "Release-candidate evidence" |

(This report and the STATUS update are a docs-only commit after it; its CI result is stated in the hand-over message, not here.)

**Read this first.**
- **Handwriting reading is still unvalidated.** Phase 5 added no handwritten data and no measurement of it. Phase 0b measured a best line error of about 0.43 on 7 real pages; the machine reading has only ever run end to end on synthetic printed pages. The product shows every machine line as "can be wrong" and never lets it near a mark. Tamil and Devanagari *handwriting* has not been tested at all (D27; Phase 0c is blocked). The pilot is what will measure it, and `pilot-cer` can only measure lines examiners corrected (below).
- **Screen reader testing: NOT done. Real tablet / phone testing: NOT done.** I produced the walk-through (`docs/PILOT_WALKTHROUGH.md`); nobody has carried it out. The claim "usable with a screen reader" is therefore not made anywhere.
- **Not run locally (first run was in CI):** the HTTPS proxy and the whole compose stack, the release-candidate job, the Trivy scan in CI. This host's Docker daemon cannot stop containers and the old stack on it is on an older schema, so compose could not be exercised here. Run locally and green: `make test` (460 + spike), `make lint`, the PDF writer tests, shellcheck-equivalent syntax checks, one Trivy scan of all 7 images, YAML parsing of `ci.yml`.

## What works

| Step | What | Commit, CI run |
|---|---|---|
| 5.0 | Self-service password change (current password needed, 12+ characters, refused if it is the old one or contains the email); admin reset gives a random temporary password shown once, ends every session of that person, and forces a change at the next sign-in (every other route answers 403). Sign-in and change throttled; a regression test proves no password reaches a log. | `40d4d16`, `38120793096` |
| 5.1 | Rubric guard: once any evaluation exists, a new rubric version needs an administrator and a written reason (10+ characters), audited with the number of grades already given. Grades stay tied to their version; the UI shows the version and says when a booklet's grades are from an earlier one. | `9c9f38f`, `38122248252` |
| 5.2 | Encrypted backups with `age` 1.3.2 (pinned, checksum-verified): nightly local backup, weekly copy encrypted to the owner's public key and shipped to an off-machine folder, retention 14 daily / 8 weekly, the private key never in the repo or the stack (the scripts refuse to run if they find one), the owner's monthly drill in the runbook. | `de9aeae`, `38123249734` |
| 5.3 | Caddy (digest-pinned) as compose profile `https`: local CA, web on 8443 and the object store on 9443, `GRADEMIND_COOKIE_SECURE=true` (Secure cookies, HSTS), per-request-nonce CSP, only the proxy publishes a port. | `26181a6` **red** `38124302825`, fix `ddf6f9e` green `38126273694` |
| 5.4 | Trivy (digest-pinned) scanning all 7 images as a non-blocking CI report with a summary on the run page; owner-readable severity table in `docs/SECURITY_AUDIT.md`; monthly image-update routine in the runbook. | `d2087c3`, `38127180929` |
| 5.5 | Tamil and Devanagari in result sheets and the summary PDF: Noto Sans fonts (OFL), vendored, checksummed, licence texts shipped; `uharfbuzz` 0.56.2 for shaping (the only new package); embedded CID fonts with the real characters stored so the text reads back. | `081d387`, `38128325247` |
| 5.6 | Pilot kit: walk-through checklist, pilot protocol with `pilot-cer`, one-page explainer. | `b0e28f3`, `38129234314` |
| 5.7 | The release-candidate E2E in CI (below). | `6c2437a` red `38130037777` and `38130341999` (both my mistakes, listed below); fixes green in `38131070292` |

Invariants held: the examiner stays the final authority (no AI verdicts); no OCR text in any mark computation (the import contract still passes; `pilot_stats.py` only reads the corrections table and nothing grading imports it); nothing leaves the machine and no cloud API was used; records stay append-only (the database refused every attempted edit of snapshots and history in the evidence run); finalized snapshots untouched.

## Release-candidate evidence (CI run `38131070292`, job `release-candidate`: a fresh stack behind HTTPS from its first start)

- HTTPS: the chain verifies against the proxy's own root, HSTS present, the plain ports 3100/8000/9000 closed, **only `grademind-proxy-1` publishes a port**. Playwright over HTTPS, 4 passed: `https` (Secure cookie, HSTS, CSP, page images from the HTTPS object store, no plain HTTP), `passwords` (own change; the other device is signed out; the old password refused; admin reset; the temporary password reaches only the change page), `rubric-guard`, `release-candidate`.
- `scripts/phase5_evidence.sh`, from the real database:
  - people the E2E created: `examiner | 3 | f | t` and `examiner | 2 | f | t` (sessions ended so far, still forced to change: no, has chosen their own: yes); nobody left holding a temporary password;
  - audit: `auth.password_changed` 3, `user.password_reset` 2; no audit entry carries a password value;
  - rubric guard: one approval after grading started, by an `admin`, 2 grades already given, a 39-character reason;
  - the finalized booklet is `finalized under v1` while v2 is the newest approved version;
  - the Tamil-named exam's reports: `report.student_pdf` snapshot 1, 185,866 bytes; `report.summary_pdf`; **`unprintable_characters` false for both**;
  - the database refused: update and delete of result snapshots, update and delete of finalization events. Last line `PHASE 5 EVIDENCE OK`.
- The sheet and the summary were read back with a PDF reader: the exam name `தமிழ் தேர்வு <number>` and the subject `हिन्दी विषय` are in the text exactly, no "?" (`OK sheet.pdf: 185866 bytes, 709 characters read back, no '?'`; `OK summary.pdf: ... 505 characters ...`).
- `verify-snapshots`: `VERIFY OK: 2 snapshot(s) recomputed from the raw records; every one reproduces exactly`.
- Encrypted backup: weekly copy made, identical off-machine copy, no plaintext inside; a **wrong key refused** (`could not decrypt ... nothing was restored`); restored **over the live data** with the right key given only to the restore: `RESTORE VERIFY OK: schema 0013; 12 stored object(s) match their recorded SHA-256; 2 result snapshot(s) recompute exactly`; services restarted; `verify-snapshots` again `VERIFY OK: 2`; the private key is not in the API container's environment.
- After the restore, over HTTPS: the administrator signs in, the exam is listed, the result sheet is made again from the restored snapshot and reads back the same (`OK sheet-after-restore.pdf`, 709 characters, no "?").

Other new evidence in `compose-smoke` (same run): Trivy table (Critical/High: caddy 0/4, minio 0/2, app 2/55, ocr 4/82, web 0/8, postgres 1/24, redis 0/0; for the two Debian images no High or Critical finding has a fix published yet), `RESTORE VERIFY OK` with 8 snapshots three ways (side copy, over live data, from the encrypted copy), 30 browser tests.

## What is tested and what is not

Tested in CI: everything above at the level stated. New tests in this phase include: 15 PDF tests for Tamil/Devanagari (exact read-back with pdfium; the page rendered and the ink measured, so a "?" or a box cannot pass; conjuncts drawn as one shape; right-aligned cells end at the same place whatever the script; wrapping inside the margins with no word lost; deterministic output; fonts embedded only when used; a font that does not match its checksum refused; a property test over the Tamil-to-Malayalam range), 8 tests of the pilot measurement and 1 database test, 3 tests of the Trivy summary, and the HTTPS, password, rubric-guard and release-candidate browser specs.

**Not tested:** a screen reader or a real device on anything; Tamil/Devanagari *handwriting*; the PDFs with a screen reader; the interface in any language but English; fonts other than Regular (headings stay Latin); a restore of data larger than the CI data (12 to 67 objects); the monthly routines (they are written, nobody has run them on a calendar); the HTTPS certificate on a real phone or laptop (the root certificate must be installed on each device; the runbook says how, nobody has done it).

## Problems found on the way (the red runs are listed, not hidden)

- `26181a6` red (`38124302825`): (a) the compose-config tests relied on a local `.env` that supplied required variables (they now supply their own); (b) the proxy healthcheck addressed the proxy by IP, which a TLS server with named sites refuses (no server name): it now checks by name. Fixed in `ddf6f9e`.
- `6c2437a` red (`38130037777`): **my mistake**: I wrote `scripts/rc_check_pdf.py` and did not run `make lint` before pushing; its docstring was over the line-length limit. Fixed in `396665b`.
- `396665b` red (`38130341999`, `compose-smoke`): **my mistake**: the plain-stack Playwright run picks up every spec, including the two new release-candidate ones, which need `E2E_RC_OUT`. They now skip unless it is set (`b5effdf`). The release-candidate job itself was green in both runs.
  (That is two red runs on the same step; they had different causes, both mine, both fixed at the cause. Neither was a flaky step and nothing was retried.)
- **A real bug found by the new Tamil test and fixed in `081d387`:** an exam whose name is in Tamil or Devanagari made the *summary download answer 500*, because the file name went into an HTTP header that can only carry Latin-1. It was there since Phase 4. Downloads now have a plain-ASCII name plus the real name in `filename*` (RFC 5987).
- **A real bug found in a CI log and fixed in `88f56df`:** every request made uvicorn's access log raise "--- Logging error ---", because the log redaction filter (D26.4) set the record's `args` to `None` and uvicorn's access formatter unpacks them. It was there since the redaction was added; the access log lines were being lost in error dumps. The filter now keeps the arguments (each text part redacted); a regression test formats a real uvicorn access line and checks the secret in the URL is masked. The following run has 0 "Logging error" lines in all three stack jobs.
- The repo's hidden-character guard caught a literal zero-width joiner in my test source (now written as an escape): the guard works.
- Student references are ASCII pseudonyms by design, so my first API test (a Devanagari reference) was wrong, not the code; Tamil and Devanagari come through exam names, subjects, question labels and rubric names.
- With a **snap-installed Docker**, Trivy's output written under `/tmp` is not where the script looks (the daemon sees a different `/tmp`): the script now warns when no report appears and the runbook says to use a folder under home.
- **D30.2 says the "hide machine reading" toggle is per user; it is stored in the browser (`localStorage`)**, so it is per browser on a device, not per account. The walk-through and the explainer say "this browser". (Decision 3 below.)

## Known gaps (not hidden)

- **Handwriting reading unvalidated** (above). **The pilot measurement cannot give an overall error rate**: the corrections table only holds lines an examiner *changed*; lines read right and left alone leave no row. `pilot-cer` therefore reports the error rate **among corrected lines**, refuses below 50 lines, says INTERIM below 200, prints numbers only, and prints that caveat with every report. Measuring all lines would need examiners to confirm unchanged lines, which the product does not do today.
- Accounts an administrator **creates** are not forced to change their password at first sign-in (D30 asked that for a reset). To force it: after creating an account, press "Reset password" and hand over that temporary password, or tell people to use Account at once.
- **Image findings**: the Debian-based images (app, OCR) have 2 and 4 Critical findings in system libraries with no fix published yet; postgres' are in the Go runtime of a helper tool (a fixed version exists: the first thing the monthly routine tries). None is shown to be reachable from outside the LAN/VPN, but this is not a penetration test, and **the stack must not be exposed to the internet**.
- HTTPS uses a local certificate authority: the root certificate has to be installed on every device. A real certificate for a real name is a separate decision.
- Backups: the weekly encrypted copy goes to a folder you choose (mount a different disk or share there); one machine is one point of failure; a lost private key means the encrypted copies cannot be opened by anyone.
- No multi-factor sign-in, no individual session revocation (a password change or reset ends all of that person's sessions); throttling trusts `X-Forwarded-For` from the proxy only (the proxy overwrites it).
- PDF reports: any script other than Latin, Tamil, Devanagari prints "?" and is flagged in the audit entry; no right-to-left; layout is a plain monospaced table.
- The web interface is English only. Accessibility is automated-checked, not human-checked.
- Unchanged from Phase 4: analytics time-to-first-grade includes time away; the exam list is unpaged; OCR is about 12 to 14 s a page on this CPU with concurrency 1.

## RELEASE CHECKLIST (the owner ticks each line before the first real booklet)

- [ ] **Users created.** Administrator account(s), teacher(s), examiners; each examiner assigned to their exams. For each new person press **Reset password** and hand over the temporary one in person, so their first sign-in forces their own password (or tell them to change it under Account at once).
- [ ] **`.env` set for HTTPS:** `GRADEMIND_HOST` (the name or address people type), `GRADEMIND_COOKIE_SECURE=true`, `GRADEMIND_S3_PUBLIC_ENDPOINT_URL=https://<same host>:9443`, `GRADEMIND_HTTPS_BIND` (the LAN/VPN address). Started with `docker compose -f compose.yaml -f compose.https.yaml --profile https up -d` (runbook, "HTTPS").
- [ ] **LAN/VPN confirmed:** from a phone on mobile data (not the college network) the address does not answer; from the college network it does; the firewall allows only 8443 and 9443 from the college network or VPN.
- [ ] **Root certificate installed** on every examiner device (runbook), and a device opens the sign-in page without a warning.
- [ ] **Backup key stored:** the `age` key pair made on a different machine, the **private key** printed or on a USB stick in the safe, never on this machine or in `.env`; the public key's file set in `GRADEMIND_BACKUP_RECIPIENTS`; the off-machine folder (`GRADEMIND_OFFSITE_DIR`) is on a different disk or share.
- [ ] **Restore drill done once by you** (runbook, "Monthly restore drill", all six lines ticked), with today's date written down.
- [ ] **Walk-through done** (`docs/PILOT_WALKTHROUGH.md`) by a screen reader user and on a real tablet and phone, signed at the bottom; or the pilot report says "NOT done".
- [ ] **D20 consent for pilot booklets recorded:** every booklet's "Data use" is set on purpose (local only by default; public release only with the student's written consent on file).
- [ ] Examiners have read `docs/WHAT_THIS_SYSTEM_DOES.md`.
- [ ] First **monthly dates** put in your calendar: restore drill and image update (`docs/RUNBOOK.md`); the first image scan table read and the owner-readable severity table in `docs/SECURITY_AUDIT.md` understood.
- [ ] `docker compose exec api python -m grademind_core.cli verify-snapshots` prints `VERIFY OK` on the data that is there (or no snapshots yet).
- [ ] Unchanged owner items, independent of this phase: `OWNER_VERIFIED` manifests (Phase 0c stays blocked, D27), D20 consent records for sheets 001 and 002, data for D23 (at least 10 students, 2 subjects, 1 numerical).

## Decisions needed from the owner

1. **Approve Phase 5 as is, or list changes.**
2. **Should the pilot be able to measure the machine over all lines?** That means an examiner confirming (or marking "correct") lines they leave unchanged: a small product change after the pilot starts, or accept that the pilot only measures corrected lines.
3. **The hide toggle:** keep it per browser, or store it per account (a small change and a migration)?
4. **Created accounts:** force a password change at first sign-in for accounts an administrator creates, as for a reset (a small change), instead of the "press Reset" workaround?
5. **A real certificate** for a real name (if the college has one) instead of the local CA, or keep the local CA and install its root on devices?
6. **When the pilot starts**, and who reads the first `pilot-cer` report (it needs 50 corrected lines to say anything, 200 for the protocol's figure).

I have not started Phase 6 and nothing was sent to any cloud service.

## After approval: D31 follow-ups (owner decisions, implemented; CI result in the hand-over message)

D31 approved Phase 5 and decided the open items. Implemented without starting any new phase:

- **D31.3 hide toggle per account:** `users.machine_reading_hidden` (migration 0014), returned by `/me`, set by `PUT /me/preferences` (that one field only; refused while a temporary password is in force). The panel asks the server, so the choice follows the person to any device. (The old per-browser choice is not migrated: each person hides it once more.)
- **D31.4 created accounts:** `POST /users` now defaults `must_change_password` to true, so a created account has the same first sign-in as a reset account. The command-line `create-user` (an operator recovery tool, also used by CI) is unchanged and does not force a change; the runbook says so. The "Reset password" workaround in the RELEASE CHECKLIST is no longer needed (the checklist line is still safe to follow).
- **D31.2 measurement:** no "mark correct" button was added. `pilot-cer` is documented as "error among corrected lines" only. New: `pilot-sample` (administrator only, audited) draws a seeded random sample of all the machine lines examiners were shown in a batch (lines inside an answer box on the latest reading of each page) into a local folder of numbered crops, with the machine's text in a separate file; `scripts/sample_transcriber.py` (stdlib, 127.0.0.1 only, never shows the machine text) lets the owner transcribe; `pilot-sample-cer` computes CER with interval and n against the machine, refusing below 50 transcribed lines. `docs/PILOT_PROTOCOL.md` section 3 and the runbook describe both numbers and what may be claimed. The sample is also the D23 data; whether it counts as `OWNER_VERIFIED` evidence under D21 is for the owner to say.
- **D31.5 / D31.6:** the local CA stays; the pilot starts when the RELEASE CHECKLIST is ticked; the owner reads the first `pilot-cer` report. Nothing to build.

## Phase 5d: pilot automation (the owner's chores, automated where safe)

No product feature was added; nothing was sent anywhere. Scripts in `scripts/pilot/`, each with `--dry-run` and `--help`, each safe to run again; `docs/PILOT_DAY.md` (new) is the owner's daily page and `docs/RUNBOOK.md` points to the scripts.

| Script | What it automates |
|---|---|
| `pilot_setup.sh` | Writes `.env` for HTTPS with random secrets (mode 600, never printed), fetches the OCR files, starts the stack with the https profile, waits until healthy, saves the local CA root to `pilot-data/ca/root.crt` after checking that HTTPS answers with a chain that verifies against it, prints the device install steps. Refuses to overwrite an existing `.env` (`--force` keeps a private copy; `--keep-env` re-runs with the existing one). |
| `pilot_users.py` | Accounts from a CSV (`email,name,role,exams`) through the admin API (sign-in as the browser does, trusting only the pilot CA); examiners assigned by exam name or id; random temporary passwords written once to a new mode-600 file (written as each account is made, never overwritten, never printed or logged); existing people left alone; the whole file is validated before anything is touched. |
| `pilot_preflight.sh` | Every machine-checkable RELEASE CHECKLIST line as PASS or FAIL (`.env` private, HTTPS settings, HTTPS reachable with HSTS, Secure cookies in the running web service, only the proxy publishes a port, `verify-snapshots`, backup recipient is a public key, **no age private key on this host** (scans `.env`, the repository, the backup and off-machine folders, `~/.config/age`, `~/.age`, Downloads, Desktop, Documents), off-machine folder on a **different device**, backup age, schedule installed, health check recent, last restore drill recorded within 35 days, administrators, examiners and assignments present). Lines a machine cannot check are printed as MANUAL and never ticked. `--record-restore-drill` writes down that the owner did the drill (an owner's record; nothing checks it). |
| `pilot_schedule.sh` | Installs, shows or removes the nightly backup (Sundays also the weekly **encrypted** copy to the off-machine folder; kept 14 daily / 8 weekly) and the daily health check, as cron entries (default) or systemd user timers; refuses a recipients file that contains a private key, a file without a public key, and an off-machine folder on the same disk as the backups. `pilot_backup_job.sh` is what the timer runs. |
| `pilot_health.sh` | Daily: every service healthy, OCR queue empty, newest backup < 26 h old, free disk space, `verify-snapshots`; **one line** appended to `pilot-data/health.log`, the same line on screen, non-zero exit on failure. No email, no cloud. |
| `pilot_report.sh` | After a batch: `pilot-cer` under the banner "ERROR AMONG CORRECTED LINES ONLY, NOT SYSTEM ACCURACY", then `pilot-sample-cer` for each transcribed sample under `~/pilot-samples/` (copied into the container for the run and removed again). Numbers only; exit 2 when a part refuses (too few lines). |

**Tested (results from CI run `38538106502` on `1f8c9c0`... see below for the final run):** 46 script tests under bash with recording stand-ins for docker, curl, openssl, crontab and systemctl (setup, schedule, backup job, health, preflight, report), 8 tests of `pilot_users.py` against a fake of the web app's sign-in and admin API, and a test that runs every SQL statement of the preflight against the real schema. **And for real, in a new CI job `pilot-automation` on a fresh runner:** setup (dry run, then the real run: `.env` mode 600, the stack up behind HTTPS, `HTTPS works`, secrets not in the output, overwrite refused, `--keep-env` again), users (2 created, temporary passwords only in a mode-600 file and not in the output, a second run `0 created, 2 already existed`, an exam created and the examiner assigned by name), the schedule (cron installed once, a private key refused), the backup job (`encrypted ... .tar.age`, the off-machine copy identical, `age-encryption.org/v1` header), the health check (`OK`, then `FAIL services not healthy: web(down)` with the web service stopped, then `OK` again), the preflight (first run: one FAIL, the missing restore drill, and the MANUAL lines; after a **real** restore drill of the encrypted copy with the key made for the test and the record: `0 FAIL`, no MANUAL line ticked) and the report (banner first, `refused`, exit 2). Evidence lines, run `38538106502`: `SUMMARY: 18 PASS, 1 FAIL, 9 MANUAL` before the drill record, `RESTORE VERIFY OK: schema 0014`.

**What stays MANUAL (by your rule, never automated):** the backup key pair (made on another machine; the scripts only accept the public key and say so), every booklet's data-use choice (D20), marking anything `OWNER_VERIFIED`, transcribing the sample (`pilot_report.sh` only reads transcriptions you made), the walk-through, installing the CA root on devices, confirming the network is closed to the outside, and doing the restore drill itself. `pilot_preflight.sh` lists each of these as MANUAL on every run.

**NOT run on a real pilot host:** all of it ran on a CI runner and under stand-ins here; none of it ran on the college machine. Specifically not exercised: a real LAN/VPN bind address (CI used `127.0.0.1`, so "listens on the LAN address" and "reachable from another device" are untested), the CA root installed on a real device, an off-machine folder that is a real second disk or a network share (CI used a RAM disk, which is a different device but not a different machine), the **systemd** timers (only the generated unit files are tested; CI used cron), a nightly cron run happening by itself at its time (CI ran the same job by hand), the daily health check running unattended, and `--force` over a real `.env` on a machine with data. This host's own Docker was not touched (the old stack on it is on an older schema and was left running); `pilot_users.py`'s TLS path (the CA root, connecting by address with the host name verified) ran only in CI.

**Found on the way:** the first real run (CI `38537604577`, job `pilot-automation`) found that the preflight's data-use query was invalid SQL (an aggregate inside GROUP BY) and aborted the script: the stand-in for docker in the unit tests cannot catch invalid SQL. Fixed in the next commit; `tests/test_pilot_sql.py` now runs every SQL statement of the preflight against the real schema so it cannot come back.

**Owner choices left open:** none new. Two small notes: the administrator's initial password stays in `.env` (mode 600) after setup, which is why the docs say to change it under Account at first sign-in (then `pilot_users.py` asks for it instead of reading `.env`); and `pilot_preflight.sh`'s "session cookie flags" line is MANUAL unless you give it the administrator's login in the environment for that run.
