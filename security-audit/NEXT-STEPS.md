# Security Remediation Handoff — Glide

**For:** the agent or engineer implementing fixes.
**Source of truth:** `security-audit/REPORT.md` (full evidence, exploit paths, confidence).
**Verified against:** `main` @ `6c0606e`, 2026-09-11.
**Mode of this document:** implementation work order. Unlike the audit, this
file authorises code changes — but only the items below.

## How to use this file

1. Read `security-audit/REPORT.md` first. Do not work from this summary alone.
2. Re-verify each anchor before editing (`git rev-parse --short HEAD` should be
   `6c0606e`, or confirm the lines still match on the current HEAD).
3. Work one item per commit, in the order given. Each item has an acceptance
   test; do not mark it done without it.
4. Never print secret values, tokens, or `.env`/`secrets/` contents into logs,
   commits, or command output.
5. Do not deploy to AWS or push unless the human explicitly asks. Template and
   script changes can be validated offline.

## Start here (execution order)

1. Read `security-audit/REPORT.md` (at least the F1–F20 sections) first.
2. Implement in this order: **S1 → S2 → S3 → S4 → S5 → S6 → S8 → S9 → S7 →
   S10 → S11**. S1 and S2 are the High-severity items; do not start with the
   low-risk cleanup.
3. One commit per item, message naming the item (`S3: query sample sessions by
   key instead of scanning`).
4. Leave the unrelated working-tree changes alone (the pre-existing docs batch,
   `.gitignore`, `scripts/verify_deployed_sample.py`, `.playwright-mcp/`).
5. Final report: one row per item — status, files, tests run, evidence, and
   anything blocked or unverified.

## Global definition of done

Run all of these after each item and once at the end:

```powershell
uv run pytest -q
uv run ruff check .
uv run python scripts/validate_template.py
```

```powershell
cd frontend
npm ci
npm run typecheck
npm run build
```

If `uv` complains about its cache directory, set
`$env:UV_CACHE_DIR` to a writable path inside the workspace first.

Also keep the project record current: update `docs/completion-progress.md` and
`docs/next-steps.md` as items land, so the docs match the code. Steps that need
network (for example a clean `npm ci`) may fail in a restricted environment — if
so, record the check as UNVERIFIED rather than skipping it silently.

## Work items, highest risk first

### S1 — Stop the production demo endpoints from creating unbounded billed work

**Fixes:** F1 (High). **Depends on:** a product decision (see Decisions).
**Files:** `backend/glide/api/app.py`, `backend/glide/deploy/api.py`,
`backend/glide/deploy/worker.py`, `backend/glide/adapters/fixtures.py`,
`backend/glide/deploy/dispatcher.py`, `infra/template.yaml`.

What is wrong: `deploy/api.py:35-41` builds the Lambda app with `create_app()`,
which always registers the demo router (`app.py:195`). `GLIDE_ENV=production`
only suppresses the module-level local app (`app.py:250`). Sample tenants are
created `enabled: True` (`fixtures.py:260`), so every anonymous
`POST /api/demo/session` produces a tenant the 5-minute dispatcher
(`deploy/dispatcher.py:74-103`) keeps re-running on Bedrock
(`infra/template.yaml:276`, `deploy/worker.py:36-40`).

Minimum acceptable fix (pick one or combine):

- Register the demo router only when the app is not serving the deployed
  environment, **or** require authentication on demo endpoints in production.
- If the public demo must stay open, make sample tenants non-scheduled:
  `enabled: False` at creation, and/or skip `sample-` users in the dispatcher,
  and route sample runs to `DeterministicAgentRunner` instead of the Bedrock
  runner in the deployed worker (this also delivers N9 from
  `docs/next-steps.md`).

**Acceptance:**

- A test builds the production entrypoint (`glide.deploy.api.build_lambda_app`
  with fakes, or `create_app(run_local_worker=False, ...)` plus
  `GLIDE_ENV=production`) and asserts demo routes are absent or auth-gated.
- A test asserts a newly created sample tenant is not picked up by
  `dispatch_once` (or is created disabled).
- `uv run pytest -q` stays green.

### S2 — Stop resolving long-lived secrets into Lambda environments

**Fixes:** F2 (High). **Depends on:** a human decision about rotation (see
Decisions). **Files:** `infra/template.yaml`, `backend/glide/api/app.py`,
`backend/glide/deploy/api.py`, `backend/glide/deploy/worker.py`.

What is wrong: `infra/template.yaml:226-227` bakes
`GLIDE_SESSION_SECRET` into the API Lambda environment via a CloudFormation
dynamic reference, and `:229`/`:281` put `GOOGLE_CLIENT_SECRET` into both
functions. `app.py:126-131` and `deploy/api.py:28-32` read them from the
environment.

Do this:

- Fetch both secrets at runtime from Secrets Manager (cache in module scope;
  refresh on cold start). Keep only the secret ARN/name in the environment.
- Extend the existing IAM policy (`AWSSecretsManagerGetSecretValuePolicy`) to
  cover the Google secret; do not widen to `*`.
- After deploy, confirm neither value appears in
  `aws lambda get-function-configuration` output.

**Acceptance:** template shows no secret value or dynamic-reference resolution
into environment variables; offline template validation passes; a unit test
fakes the Secrets Manager client and proves the app starts without
`GLIDE_SESSION_SECRET`/`GOOGLE_CLIENT_SECRET` set, reading them from the fake
client instead.

### S3 — Remove the full-table scan from session lookup

**Fixes:** F4 (Medium). **Files:**
`backend/glide/adapters/dynamodb.py`, `backend/glide/adapters/sqlite.py`,
`backend/glide/api/demo_store.py`, `infra/template.yaml`,
`scripts/validate_template.py`.

What is wrong: any `X-Glide-Session` value reaches
`get_sample_snapshot_by_session` (`dynamodb.py:375-382`), which iterates
`_scan()` (`:121-132`) over the whole table.

Do this: store sessions under a queryable key (e.g. `pk = SESSION#<id>`) or add
a `session_id` GSI and query it. Sample snapshots expire in 24 h
(`demo_store.py:97`), so a backfill is likely unnecessary — state that
assumption in the commit message.

**Acceptance:** with a fake DynamoDB client, a request carrying
`X-Glide-Session: unknown` records **0 Scan** calls and a bounded Query; the
existing durable-session tests pass.

### S4 — Add access logging and throttling to the HTTP API

**Fixes:** F3 (Medium). **Files:** `infra/template.yaml`,
`scripts/validate_template.py`.

Do this: add JSON `AccessLogSettings` on the stage (reuse the 30-day retention
pattern at `:187-203`) and `DefaultRouteSettings` throttling; consider a
tighter route limit for `POST /api/demo/session`. If WAF is added, note that a
CloudFront-scoped `AWS::WAFv2::WebACL` must be created in `us-east-1` and
attached via the distribution — keep that in a separate commit.

**Acceptance:** `scripts/validate_template.py` asserts the log group,
format, and throttling settings exist; `sam validate --lint` passes if
available.

### S5 — Restrict CORS origins in production

**Fixes:** F5 (Medium). **File:** `backend/glide/api/app.py:181-192`.

Do this: include the localhost dev origins only when not in production; in
production allow exactly `GLIDE_FRONTEND_ORIGIN`.

**Acceptance:** a test with `GLIDE_ENV=production` asserts the CORS middleware
origin list is exactly the configured origin; the existing app-config tests
pass.

### S6 — Harden CI supply chain

**Fixes:** F6 (Medium). **File:** `.github/workflows/ci.yml`.

Do this: add an explicit least-privilege `permissions:` block
(`contents: read` unless more is proven necessary); pin
`actions/checkout`, `astral-sh/setup-uv`, and `actions/setup-node` to commit
SHAs (keep the version in a trailing comment); optionally add a Dependabot
config or a scheduled dependency-audit job (network required in CI only).

**Acceptance:** workflow parses; a PR touching the repo shows a green run with
the new permissions; no `write` permission remains unless justified.

### S7 — Contain MCP credential exposure

**Fixes:** F7 (Medium, local tooling). **Files:** `tools/mcp/**`,
`scripts/install-mcps.ps1`, `docs/mcp-setup.md`.

Do this:

- Pin the `@playwright/mcp`, `@modelcontextprotocol/server-github`, and
  `@zetalytics/mcp-google-calendar` specs to exact versions and refresh the
  lockfiles (`npm install --package-lock-only`).
- Pin the four `ghcr.io/awslabs/mcp/*` images by digest in both the config and
  `install-aws-mcps.ps1`.
- Use a least-privilege AWS SSO profile and a read-only GitHub PAT for routine
  work; document the minimum scopes.
- Make the staged `tools/mcp/playwright/mcp-section.toml` and the installed
  `.codex/config.toml` agree on approval mode (prefer `"writes"`, not
  `"auto"`); re-run `scripts/install-mcps.ps1` if the installer is the source
  of truth.

**Acceptance:** no `"latest"` specs remain; every container reference has
`@sha256:`; setup docs state the minimum scopes; both Playwright configs match.

### S8 — Stop passing the Google client secret on the command line

**Fixes:** F8 (Medium). **Files:** `scripts/deploy.ps1`, `infra/template.yaml`.

Do this: keep the client secret in Secrets Manager (or read it from the
process environment inside the script) and pass only its name/ARN through
`sam deploy`. Do not echo it.

**Acceptance:** no `--parameter-overrides` entry contains a secret value;
deploy docs are updated to the new flow.

### S9 — Add alarms for failures, queue depth, and spend

**Fixes:** F9 (Medium). **File:** `infra/template.yaml`.

Do this: alarm on DLQ depth ≥ 1, worker errors ≥ 1, API 5xx/throttles; add an
AWS Budgets notification at the documented USD 75 ceiling (budget resources
may need a separate stack if SAM's `AWS::Budgets::Budget` is preferred).

**Acceptance:** alarms exist in the template with an SNS or email target
configured through a parameter; template validation passes.

### S10 — Decide on session revocation

**Fixes:** F10 (Medium, needs a decision). **Files:**
`backend/glide/api/auth.py`, `backend/glide/adapters/*`,
`backend/glide/live/disconnect.py`.

Options: shorten the 7-day session; add a per-user `revoked_at` /
session-version check in the state store; or accept the risk and document it.
If implemented, make logout/revoke bump the version so old cookies fail on
decrypt or on the next request.

**Acceptance:** a test proves a cookie captured before logout is rejected
after logout.

### S11 — Quick hardening (one commit each or grouped)

- **F13:** `deploy/worker.py:94` — log only `messageId` and the error type,
  never the full SQS record.
- **F15:** `frontend/src/api.ts:22-40` — retry only idempotent verbs, or add a
  client-generated idempotency key honoured by `POST /api/runs`.
- **F16/F17:** bound `time_zone` (validate against `ZoneInfo`) and place
  labels; map repair-prompt rejections to a fixed reason-code enum.
- **F12/F14:** avoid logging place ids with `run_id`; demote or hash.
- **F18:** enable DynamoDB point-in-time recovery (and consider a CMK).
- **F20:** keep `.codex/` ignored; replace absolute owner paths in published
  MCP config with a placeholder or relative path.

## Decisions the human must make

1. **Public demo policy (S1):** keep the anonymous sample demo, or restrict it
   to dev? Keeping it requires throttling plus sample-run isolation from
   Bedrock; restricting it changes the submitted UX.
2. **Secret rotation (S2):** if Lambda environment variables may already have
   been read by anyone, rotate the session key and Google client secret after
   the fix.
3. **WAF (S4):** adding CloudFront-scoped WAF adds cost and a `us-east-1`
   resource; confirm before implementing.
4. **Session model (S10):** stateless 7-day sessions vs. revocable sessions.

## Ordering and sizing

| Order | Item | Risk removed | Rough size |
| ----- | ---- | ------------ | ---------- |
| 1 | S1 | High (cost/abuse) | S–M |
| 2 | S2 | High (credential blast radius) | M |
| 3 | S3 | Medium (DoS/cost) | S |
| 4 | S4 | Medium (abuse detection) | S–M |
| 5 | S5 | Medium (origin trust) | XS |
| 6 | S6 | Medium (CI) | S |
| 7 | S8 | Medium (secret hygiene) | XS |
| 8 | S9 | Medium (detection) | S |
| 9 | S7 | Medium (local tooling) | M |
| 10 | S10 | Medium (needs decision) | M |
| 11 | S11 | Low | S each |

## Do not do

- Do not commit `.env`, `secrets/`, `credentials*.json`, `*.db`, `temp/`,
  `.codex/`, or `test-results/`.
- Do not paste secret values into commit messages, PR descriptions, or logs.
- Do not weaken the existing validation in
  `backend/glide/agent/host.py`/`domain/live.py` while refactoring.
- Do not remove the SQS DLQ, KMS encryption, or least-privilege IAM statements
  that already exist.
- Do not deploy or push without explicit human approval.

## Known caveats from the audit

- Findings are anchored to `6c0606e`; re-verify line numbers first.
- The audit was offline. Python CVE status and live AWS state were never
  verified — do not treat them as clean.
- During the audit window, `.gitignore` and
  `scripts/verify_deployed_sample.py` changed in the working tree, and
  `.playwright-mcp/` appeared. Confirm current state before editing these
  files; see "Coverage and method notes" in `security-audit/REPORT.md`.
