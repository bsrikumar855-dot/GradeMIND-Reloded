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
| **Back up** | Once, by hand: `./scripts/backup.sh` (writes `backups/<UTC time>/`). The routine is automatic: see **Backups** below (nightly local, weekly encrypted copy off the machine, 14 daily / 8 weekly kept). Always back up before an upgrade. |
| **Check a backup works (a drill)** | The owner does this **monthly**: see the checklist below. In short: `./scripts/restore.sh <backup> restore_check gm-restore-check` (add `--identity <key file>` for an encrypted `.tar.age`), and look for `RESTORE VERIFY OK`. |
| **Recover from loss** | Keep `postgres` and `minio` running; `docker compose stop api worker ocr-worker web`; `./scripts/restore.sh <backup or .tar.age> grademind grademind --replace-live [--identity <key file>]` (it **drops** the live database and overwrites objects); `docker compose up -d`; `./scripts/compose_wait.sh`. Results since the backup are lost: say so to the examiners. |
| **Prove the results are intact** | `docker compose exec api python -m grademind_core.cli verify-snapshots` recomputes every finalized result from the raw grades. `VERIFY OK` means every one reproduces; anything else lists the differences (exit 1): stop, keep the database as it is, and report it. |
| **Export examiner corrections** | `docker compose exec api python -m grademind_core.cli export-corrections --as-admin <admin email> --scope local --out /tmp/corrections` is an administrator-only command; read `docs/DATASET_EXPORT.md` first (consent scope, where it may be written, never into the repository). |
| **The text-reading (OCR) service is down** | Grading is **not** affected: booklets can be graded as soon as their pages are rendered. Booklets show "Unread" / "Trying again by itself": the system retries 4 times over about 25 minutes. `docker compose logs --tail=50 ocr ocr-worker`; `docker compose restart ocr ocr-worker`. When it is healthy, press **Retry machine reading** on any booklet still marked Unread. Handwriting reading is unvalidated; examiners must always check it against the page. |
| **Someone is locked out of sign-in** | Five wrong passwords from one place lock that account **from that place** for 15 minutes (Retry-After is sent). The administrator can wait, or deactivate and re-create the account from **Users**. |
| **Booklet pages show as blank boxes** | The browser fetches page images from the object store's own address, and the web app's security policy only allows that one address. `GRADEMIND_S3_PUBLIC_ENDPOINT_URL` must be set to the **same** value for the api and the web service (compose does this from `.env`); if it differs from the address the browser reaches, images are blocked. |
| **Upgrade** | Back up, `git pull`, `docker compose up -d --build` (migrations run by themselves), `./scripts/compose_wait.sh`, then `verify-snapshots`. |
| **HTTPS (the pilot way)** | See **HTTPS** below. Short: set `GRADEMIND_HOST`, `GRADEMIND_COOKIE_SECURE=true`, `GRADEMIND_S3_PUBLIC_ENDPOINT_URL=https://<host>:9443` and `GRADEMIND_HTTPS_BIND=<LAN or VPN address>` in `.env`, then `docker compose -f compose.yaml -f compose.https.yaml --profile https up -d`. |

## HTTPS (D30.5)

The pilot runs on a **LAN or VPN only**. A reverse proxy (Caddy, pinned by digest, compose profile `https`) puts HTTPS in front of it and is the **only** thing that listens outside the Docker network: the web app on **8443**, the object store on **9443** (booklet page images come from it, and an HTTPS page may not load images over HTTP). The plain ports (3100, 8000, 9000) are closed by `compose.https.yaml`.

1. In `.env`: `GRADEMIND_HOST=grademind.lan` (the name or fixed address people type; it goes into the certificate), `GRADEMIND_COOKIE_SECURE=true` (secure cookies and HSTS), `GRADEMIND_S3_PUBLIC_ENDPOINT_URL=https://grademind.lan:9443`, and `GRADEMIND_HTTPS_BIND=192.168.1.20` (the LAN or VPN address of this machine; the default `127.0.0.1` listens on this machine only).
2. Start: `docker compose -f compose.yaml -f compose.https.yaml --profile https up -d`, then `./scripts/compose_wait.sh`. Open `https://grademind.lan:8443`.
3. The certificate comes from the proxy's own local certificate authority, so every device must be told to trust its **root certificate** once (a certificate, not a key): `docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./grademind-root-ca.crt`, then install that file as a trusted root on each laptop, tablet and phone (the browser or system certificate settings). Without it browsers show a warning; do **not** teach people to click through it. The CA lives in the `caddydata` volume: keep it in your backups' scope (it is not student data) and do not delete the volume, or every device must trust a new root.
4. Firewall: allow 8443 and 9443 from the LAN or VPN only; do not forward them from the internet. Sign-in throttling uses the real client address because the proxy overwrites `X-Forwarded-For`.
5. To go back to plain HTTP on this machine only: `docker compose up -d` (without the second file and the profile), and unset the three variables.

## Backups (D30.4)

**What runs.** Put one line in cron on this machine (night time): `17 2 * * * cd /path/to/GradeMIND-v2 && ./scripts/backup_rotate.sh >> backups/rotate.log 2>&1`, with these set in the cron environment or a wrapper:
`GRADEMIND_BACKUP_RECIPIENTS=/path/to/recipients.txt` (the owner's **public** key, one `age1...` line) and `GRADEMIND_OFFSITE_DIR=/path/to/the/off-machine/folder` (a mounted drive, or a folder that an rsync/rclone job ships away).
- **Every night:** a local backup in `backups/daily/<UTC time>/`; the newest **14** are kept.
- **Every Sunday:** an **encrypted** copy `backups/weekly/<UTC time>.tar.age`, encrypted with `age` to the owner's public key, copied to the off-machine folder with a checksum; the newest **8** are kept in both places. (`--weekly` forces one.)
- The tool is `age` v1.3.2, fetched once with `./scripts/fetch_age.sh` (checksum-verified, into `tools/`, which git ignores).

**The key.** The owner makes it once, **on the owner's own machine, not this one**: `age-keygen -o grademind-backup.key` prints `Public key: age1...`. Put only that public line in `recipients.txt`. Keep `grademind-backup.key` offline (a password manager, plus a printed copy in a safe place):
**without it the encrypted copies cannot be opened by anyone, including us.** Never put it in `.env`, the repository, a chat message or this machine; the backup scripts refuse to run if they find one in `.env`, and CI checks the stack's environment and the repository for one.

**Monthly restore drill (the owner; about 20 minutes).** Tick each line and write the date down.
1. `ls -lh backups/weekly` and the off-machine folder: the newest weekly copy is no more than 8 days old, and both places hold the same file (`sha256sum -c` the `.sha256` next to it).
2. On this machine: `./scripts/restore.sh <the .tar.age> restore_check gm-restore-check --identity /path/to/grademind-backup.key`. It prints `RESTORE VERIFY OK: ...` with the counts, or `RESTORE VERIFY FAILED` (stop and report: do not rely on that backup).
3. Compare the printed row counts with the exam list: the number of exams and booklets is what you expect.
4. Try the **wrong** key once on purpose: it must refuse (`could not decrypt`), and no `restore_check` database must appear.
5. Clean up the drill copy: `docker compose exec postgres dropdb -U grademind restore_check` (and delete the `gm-restore-check` bucket in the object store console if you want the space back).
6. Confirm the key is still where it should be (you can open the file and see `AGE-SECRET-...` at the start) and the printed copy is still in the safe.

Where things are: web `127.0.0.1:3100`, API `127.0.0.1:8000`, object store `127.0.0.1:9000`. Every administrative action is in each exam's **Audit trail**.
Security review and dependency audit: `docs/SECURITY_AUDIT.md`. Accessibility: `docs/ACCESSIBILITY.md`.
