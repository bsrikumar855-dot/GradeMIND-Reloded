# GradeMIND operator runbook

One page. Run everything from the repository folder on the machine that hosts the stack. Student work lives only in this machine's Docker volumes
(`pgdata`, `miniodata`): **if you have no backup, you have no copy.** Nothing here sends anything to a cloud service.

| I want to | Do this |
|---|---|
| **First start** | `cp .env.example .env`, fill every value (long random secrets; the admin password is at least 12 characters), `./scripts/fetch_ocr_vendor.sh` once, then `docker compose up -d --build`. Open `http://127.0.0.1:3100`. |
| **Stop** | `docker compose stop` (data is kept). `docker compose down` removes containers but keeps the volumes. **Never** `docker compose down -v`: it deletes all student data. |
| **Start again** | `docker compose up -d`, then `./scripts/compose_wait.sh` until every service is healthy. |
| **See what is wrong** | `docker compose ps`, `docker compose logs --tail=100 api worker ocr-worker`. The status card on the dashboard shows database, job queue, storage and the text-reading service. |
| **Add a user, assign an examiner** | Sign in as the administrator: **Users** (create, deactivate), then an exam's **Examiners** tab. Only if no administrator can sign in: `docker compose exec -e GRADEMIND_USER_PASSWORD='...' api python -m grademind_core.cli create-user --org "<organisation>" --email a@b.in --role admin --name "Name"`. |
| **Back up** | `./scripts/backup.sh` (writes `backups/<UTC time>/`: database dump, every stored object, row counts, checksums). Do it **daily and before any upgrade**, then copy the folder to an **encrypted** disk that is not this machine. It holds student work: owner-only permissions, never shared, never committed (git ignores `backups/`). |
| **Check a backup works (a drill)** | `./scripts/restore.sh backups/<folder> restore_check gm-restore-check`. It restores into a side database and bucket, then prints `RESTORE VERIFY OK` only if the schema, the row counts, every stored file's recorded SHA-256 and every finalized result (recomputed from the raw grades) all match. Do this monthly. Drop the copy afterwards: `docker compose exec postgres dropdb -U grademind restore_check`. |
| **Recover from loss** | Keep `postgres` and `minio` running; `docker compose stop api worker ocr-worker web`; `./scripts/restore.sh backups/<folder> grademind grademind --replace-live` (it **drops** the live database and overwrites objects); `docker compose up -d`; `./scripts/compose_wait.sh`. Results since the backup are lost: say so to the examiners. |
| **Prove the results are intact** | `docker compose exec api python -m grademind_core.cli verify-snapshots` recomputes every finalized result from the raw grades. `VERIFY OK` means every one reproduces; anything else lists the differences (exit 1): stop, keep the database as it is, and report it. |
| **Export examiner corrections** | `docker compose exec api python -m grademind_core.cli export-corrections --as-admin <admin email> --scope local --out /tmp/corrections` is an administrator-only command; read `docs/DATASET_EXPORT.md` first (consent scope, where it may be written, never into the repository). |
| **The text-reading (OCR) service is down** | Grading is **not** affected: booklets can be graded as soon as their pages are rendered. Booklets show "Unread" / "Trying again by itself": the system retries 4 times over about 25 minutes. `docker compose logs --tail=50 ocr ocr-worker`; `docker compose restart ocr ocr-worker`. When it is healthy, press **Retry machine reading** on any booklet still marked Unread. Handwriting reading is unvalidated; examiners must always check it against the page. |
| **Someone is locked out of sign-in** | Five wrong passwords from one place lock that account **from that place** for 15 minutes (Retry-After is sent). The administrator can wait, or deactivate and re-create the account from **Users**. |
| **Upgrade** | Back up, `git pull`, `docker compose up -d --build` (migrations run by themselves), `./scripts/compose_wait.sh`, then `verify-snapshots`. |
| **HTTPS** | Put a reverse proxy that terminates HTTPS in front of the web port, set `GRADEMIND_COOKIE_SECURE=true` in `.env` (turns on secure cookies and HSTS), and make the proxy overwrite `X-Forwarded-For` (sign-in throttling uses it). Without a proxy, bind to this machine only (the default). |

Where things are: web `127.0.0.1:3100`, API `127.0.0.1:8000`, object store `127.0.0.1:9000`. Every administrative action is in each exam's **Audit trail**.
Security review and dependency audit: `docs/SECURITY_AUDIT.md`. Accessibility: `docs/ACCESSIBILITY.md`.
