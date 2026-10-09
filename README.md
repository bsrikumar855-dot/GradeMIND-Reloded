# GradeMIND-Reloded
GradeMIND v2: examiner-assisted grading of handwritten answer scripts (scope: [docs/DECISIONS.md](docs/DECISIONS.md) D18/D19).
Status: [STATUS.md](STATUS.md). Spec: [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md).

## Run the stack locally

```bash
cp .env.example .env    # then replace every value (see the comment at the top of the file)
make ocr-vendor         # vendored Paddle wheels + OCR weights (sha256-verified release assets)
docker compose up -d --build
python3 scripts/compose_smoke.py    # end-to-end check; exit code 0 = pass
```

The web UI is on http://127.0.0.1:3100 (`GRADEMIND_WEB_PORT`), the API on http://127.0.0.1:8000 (`/health/*`); MinIO is published on 127.0.0.1:9000 only so the browser can fetch
short-lived signed URLs. The first administrator comes from `GRADEMIND_ADMIN_*` in `.env`.

### Docker access (read this on a shared machine)

Membership of the `docker` group is **root-equivalent**: anyone in it can mount the host filesystem into a container
as root. On a shared machine, prefer [rootless Docker](https://docs.docker.com/engine/security/rootless/) or run
Docker commands with `sudo` instead of adding accounts to the group.

If you were added to the group (`sudo usermod -aG docker $USER`), the membership only applies to **new** logins. Until
you log out and back in, run Docker commands through `sg`:

```bash
sg docker -c "make smoke"
```

`scripts/compose_wait.sh` stops at once with this hint when it cannot reach Docker (instead of waiting for a timeout).

## Tests

`make test` (rule 13: prints both exit codes). DB, storage and API tests need Postgres and MinIO, given by
`GRADEMIND_TEST_DATABASE_URL` and `GRADEMIND_TEST_S3_*`; without them those tests skip. CI provides both.
