# Security pass (Phase 4 step 4.6)

Scope: the API, the web app, the compose deployment and the dependency set, reviewed on 2026-10-10 against what a local-only pilot needs. This is a
review by the developer with automated checks, **not** an independent penetration test. Nothing here claims the system is secure; it records what was
checked, what was changed, what was found and what is left.

## What was reviewed, and what changed

| Area | Finding | Action |
|---|---|---|
| Sign-in guessing | No limit on attempts. | **Throttled**: 5 failures per (account, address), 30 per address, 50 per account, in 15 minutes; 429 with `Retry-After`; the correct password is refused while a limit is in force; the moment a limit is reached is audited (no account name in it). The throttle lives in the database, so it survives restarts and covers every API process. 13 tests. |
| Account lock-out by a stranger | A per-account limit would let anyone lock a user out. | The small limit is per (account, address); the account-wide limit is large (50) so one stranger cannot lock the real user out; tested. |
| Account enumeration by timing | A miss returned before any password hash was computed. | A hash (a dummy one for a missing account) is always checked; tested. Error text is identical for every failure (already the case). |
| Client address | The API saw only the web app's address. | The web app passes the address it was reached from; the API trusts it only when `GRADEMIND_TRUST_FORWARDED_FOR=true` (set for the compose API) and uses the last value. A request from outside that sends its own header is ignored when trust is off (tested). |
| Security headers, web | `nosniff`, `X-Frame-Options`, referrer and permissions policy only. | Added a **per-request-nonce Content-Security-Policy** (`script-src 'self' 'nonce-…' 'strict-dynamic'`, no inline scripts, `object-src 'none'`, `frame-ancestors 'none'`, images only from self and the object store origin), `Cross-Origin-Opener-Policy`, and HSTS when HTTPS is configured. A browser test proves an injected inline handler and a `javascript:` link are blocked and that nothing in the main flow violates the policy. |
| Security headers, API | None. | `nosniff`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, a deny-everything CSP, `Cross-Origin-Resource-Policy` on every response (tested). |
| API docs | `/docs`, `/redoc`, `/openapi.json` listed every route. | Off when `GRADEMIND_ENV=prod` (the compose default); on in development and tests. Checked in the compose smoke test. |
| Session expiry | Tokens last 1 hour (`jwt_ttl_seconds`, 60 s to 24 h allowed); the cookie lives as long as the token. | Reviewed, kept. Tests prove an expired token, a token signed with another key and an `alg: none` token are refused. Deactivating a user ends their session on their next request (tested in 4.1). **Limit:** a token cannot be revoked individually before it expires. |
| Cookies | `httpOnly`, `SameSite=Lax`, `Secure` when `GRADEMIND_COOKIE_SECURE=true`. | Reviewed, kept. State-changing proxy calls must be same-origin (existing CSRF check, tested in the smoke test). |
| Uploads, logs, secrets | Content sniffing, size limits, log redaction, secret scanning (earlier phases). | Re-read; unchanged. |
| Backups | None. | `scripts/backup.sh` / `restore.sh` and a CI drill (below). The backup is not encrypted by this software: store it on an encrypted disk. |

## Dependency audit (2026-10-10)

| Set | Tool | Result |
|---|---|---|
| Python workspace (83 packages, from `uv.lock`) | `pip-audit` 2.10.1 against the PyPI advisory database | **No known vulnerabilities.** |
| OCR service (`services/ocr/requirements.lock.txt`) | `pip-audit` 2.10.1 | **No known vulnerabilities** in the 80 audited packages. `paddlepaddle` 3.4.0 is **not on PyPI** (vendored wheel), so it could not be audited. |
| Web (454 packages, `pnpm-lock.yaml`) | `pnpm audit` (pnpm 12.10.1) | **1 high**: `braces <= 3.0.3` (GHSA-vfj7-8cjw-p6xm, stack exhaustion on deeply nested glob patterns), reached only through `eslint-config-next > @next/eslint-plugin-next > fast-glob > micromatch`. It is a **development-time lint dependency**: it is not in the runtime image (the standalone build ships no dev dependencies) and it only ever expands this repository's own patterns. **No patched version exists** (`patched_versions: null`). Accepted; re-check when `eslint-config-next` moves. |
| Container images | Pinned by digest (D26). | **No image scanner was run.** A gap: scan the images (for example with Trivy or Grype) before a pilot, and rebuild when base images get fixes. |

CI runs both audits on every build as a **report** (`audit` artifact, not a gate: a new advisory elsewhere must not block unrelated work). Read it when it changes.

## Backup and restore drill

CI backs up the live stack, restores it into a side database and bucket and checks it, then stops the services, restores over the live data
(`--replace-live`) and starts them again. A restore counts as good only when the schema revision, every table's row count, every stored page image,
booklet source, paper source and line crop (against the SHA-256 the database recorded) and every finalized result (recomputed from the raw grades) match.
The object archive detects a damaged member, a missing member, a swapped member and a truncated archive (8 tests). The dump and the row counts are taken from **one database snapshot** (`pg_dump --snapshot` plus a count in the same session), because the first CI drill failed on exactly this: the counts were taken a moment after the dump, a machine reading finished in between, and the restore looked one row short.

## What is NOT covered (a listed gap)

- **No independent penetration test**, no fuzzing of the API, no review of the Docker host or network.
- **No encryption at rest** by this software (database, object store, backups): use full-disk encryption on the machine and the backup disk.
- **No TLS in the stack**: put a reverse proxy in front (see the runbook). Throttling trusts the `X-Forwarded-For` the web app receives; with no proxy in
  front, a caller who can reach the web port directly can choose that value, which weakens the per-address limits (the account-wide limit still holds). The
  default binds to `127.0.0.1` only.
- **No individual session revocation** and no multi-factor sign-in.
- **Container images are not scanned** (see above) and `paddlepaddle` is not auditable through PyPI.
- **Roles are coarse** (administrator, teacher, examiner); an administrator can see and do everything in their organisation, including exporting reports.
