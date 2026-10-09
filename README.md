# GradeMIND-Reloded
GradeMIND v2: examiner-assisted grading of handwritten answer scripts (scope: [docs/DECISIONS.md](docs/DECISIONS.md) D18/D19).
Status: [STATUS.md](STATUS.md). Spec: [docs/MASTER_PROMPT.md](docs/MASTER_PROMPT.md).

## Run the stack locally

```bash
cp .env.example .env    # then replace every value (see the comment at the top of the file)
docker compose up -d --build
python3 scripts/compose_smoke.py    # end-to-end check; exit code 0 = pass
```

The API is on http://127.0.0.1:8000 (`/health/*`); MinIO is published on 127.0.0.1:9000 only so the browser can fetch
short-lived signed URLs. The first administrator comes from `GRADEMIND_ADMIN_*` in `.env`.

## Tests

`make test` (rule 13: prints both exit codes). DB, storage and API tests need Postgres and MinIO, given by
`GRADEMIND_TEST_DATABASE_URL` and `GRADEMIND_TEST_S3_*`; without them those tests skip. CI provides both.
