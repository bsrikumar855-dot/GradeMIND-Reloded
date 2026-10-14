# Pilot day: what to look at, and what to do

For the owner, during the pilot. Everything here is on **this machine**: no email, no cloud notices. If you do not look, nobody tells you. Run the commands from the repository folder.
The scripts are in `scripts/pilot/`; each has `--dry-run` (it says what it would do and changes nothing) and `--help`.

## Once, before the first real booklet

1. `scripts/pilot/pilot_setup.sh --host <the name people type> --bind <this machine's LAN/VPN address> --org "<college>" --admin-email <you>`: writes `.env` (random secrets, mode 600), starts the HTTPS stack, saves the CA root to `pilot-data/ca/root.crt`, prints how to install it on devices. It refuses to overwrite an existing `.env` (`--keep-env` re-runs it with the one you have, `--force` replaces it and keeps a private copy).
   The administrator's password is in `.env`: read it there once, sign in, change it under **Account**.
2. **On another machine** make the backup key pair: `age-keygen -o grademind-backup.key`. It prints `Public key: age1...`. Put only that line in a file on this machine (for example `~/recipients.txt`). The private key stays off this machine (a password manager, a printed copy in a safe). The scripts refuse a file that holds a private key.
3. `scripts/pilot/pilot_schedule.sh install --recipients ~/recipients.txt --offsite <a folder on ANOTHER disk or share>`: the nightly backup (Sundays also the encrypted weekly copy to that folder; kept 14 daily / 8 weekly) and the daily health check, as cron entries. It refuses an off-machine folder on the same disk as the backups. (`--systemd` for systemd user timers instead.)
4. Write the people in a CSV (`email,name,role,exams`; role is `admin`, `teacher` or `examiner`; `exams` is only for examiners, names separated by `;`) and run `scripts/pilot/pilot_users.py users.csv`: it asks for the administrator's email and password (typed, never saved). Right after setup `--admin-from-env-file .env` works too; once you have changed the administrator's password under Account, `.env` no longer has the current one, so type it. It creates the accounts with random temporary passwords and writes them **once** to `pilot-data/temp-passwords-<time>.txt` (mode 600). Hand each over in person, then **delete that file**. Each person must choose their own password at first sign-in. Run it again after creating the exams to assign examiners; people who already exist are left alone.
5. Do the restore drill yourself (RUNBOOK, "Monthly restore drill"), then record it: `scripts/pilot/pilot_preflight.sh --record-restore-drill`.
6. `scripts/pilot/pilot_preflight.sh`: every line of the RELEASE CHECKLIST a machine can check, as PASS or FAIL, and the lines it cannot (walk-through, D20 consent, examiners read the explainer, the certificate on every device, LAN/VPN reachability, the key stored safely) as **MANUAL**. A script never ticks a MANUAL line for you. The pilot starts when there is no FAIL and you have done every MANUAL line.

## Every day (two minutes)

`tail -3 pilot-data/health.log`: one line per run, written at 07:30 by default.
- `... OK services=healthy ocr_queue=0 backup_age_h=5 free[...]=...% verify=ok`: nothing to do.
- `... FAIL <what> | ...`: the first thing after the word FAIL is the problem. Then:

| The line says | Do this |
|---|---|
| `services not healthy: x(unhealthy)` | `docker compose ps`, then `docker compose logs --tail=100 x`. `docker compose restart x`. OCR slow under load can look unhealthy for a while: it does not stop grading (RUNBOOK, "The text-reading service is down"). |
| `OCR queue has N waiting` | Pages are waiting to be machine-read. Normal during a big upload; if it stays after an hour, `docker compose logs --tail=50 ocr ocr-worker`. Grading is not affected. |
| `newest backup is Nh old` / `no nightly backup found` | `tail -20 pilot-data/backup.log`. Run `scripts/pilot/pilot_backup_job.sh` by hand. Check the cron entries: `scripts/pilot/pilot_schedule.sh status`. |
| `low disk space on ...` | Free space before the next backup fails: delete old exports and old backup folders you no longer need, move the oldest off-machine copies elsewhere. Do not delete Docker volumes. |
| `verify-snapshots FAILED` | **Stop.** Do not finalize or reopen anything. Keep the database as it is. Run `docker compose exec api python -m grademind_core.cli verify-snapshots` and keep its output, and report it. |
| no new line today | The schedule is not running (machine off, cron removed). `scripts/pilot/pilot_schedule.sh status`; run `scripts/pilot/pilot_health.sh` by hand. |

If the machine was off overnight, the nightly backup did not run: run `scripts/pilot/pilot_backup_job.sh` as soon as it is on.

## Every week

- `ls -lh backups/weekly` and the off-machine folder: the newest encrypted copy is no more than 8 days old, in both places.
- Read the accounts: `scripts/pilot/pilot_preflight.sh | grep -E "INFO|FAIL"` (accounts still on a temporary password; booklets by data-use choice).

## After a batch

`scripts/pilot/pilot_report.sh`: prints the error among **corrected lines only** under a banner that says it is not system accuracy, and, for each transcribed sample under `~/pilot-samples/`, the sample's error rate. It refuses below 50 lines. Numbers only: it never prints student text. Draw and transcribe the sample first (RUNBOOK, "Measure the machine's real accuracy"). What may be claimed: `docs/PILOT_PROTOCOL.md`.

## Every month (the same day)

1. The restore drill (RUNBOOK, "Monthly restore drill"), then `pilot_preflight.sh --record-restore-drill`. The preflight FAILS if the last record is older than 35 days.
2. The image update (RUNBOOK, "Monthly image update").
3. `scripts/pilot/pilot_preflight.sh` again.

## What is NOT automated (on purpose)

The backup key pair (made on another machine); every booklet's data-use choice (D20); marking anything `OWNER_VERIFIED`; transcribing the sample; the walk-through; installing the certificate on devices; confirming the network is closed to the outside; doing the restore drill itself. The scripts only check what a machine can check and say so for the rest.
