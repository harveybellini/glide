# Glide security remediation — S1–S11 status

**Date:** 2026-09-11
**Working tree:** `6c0606e` + this remediation (uncommitted in place; see
"Landing these commits")
**Order followed:** S1 → S2 → S3 → S4 → S5 → S6 → S8 → S9 → S7 → S10 → S11

## Landing these commits

The agent sandbox denies writes to the repository's `.git` directory
(`git commit` fails with `Permission denied` creating `.git/index.lock`, and
the ACL blocks the create), so the ten item commits were created in a
throwaway clone seeded from the repository, and the working tree here carries
the same changes:

- Mirror clone (real commits, based on `6c0606e`):
  `C:\Users\harve\AppData\Local\Temp\glide-mirror-1fb825e0`
- Bundle of the same commits: `%TEMP%\glide-remediation-<time>.bundle`
  (recreate with `git -C <mirror> bundle create <file> main` if the temp
  directory is cleaned up)
- Per-item patches: `security-audit/patches/0001..0010-*.patch`
  (plus `0011-Document-the-S1-S11-remediation-status.patch`)

The patches were replayed onto `6c0606e` in a scratch branch and produced a
byte-identical tree (`git rev-parse HEAD^{tree}` matches the mirror's `main`),
so they reproduce the committed state exactly (commit ids differ, as patch
application re-stamps the commits).

```powershell
# One command - fetch the history and move the branch to it, leaving any other
# uncommitted work in the tree:
powershell -ExecutionPolicy Bypass -File security-audit/apply-remediation.ps1 -Apply

# Without -Apply it only fetches and prints the review commands. The same
# thing by hand, using the mirror or the bundle:
git fetch "C:\Users\harve\AppData\Local\Temp\glide-mirror-1fb825e0" main:security-remediation
git reset --mixed security-remediation

# Option B - replay the patches on a clean checkout of the audit snapshot
git checkout -b security-remediation 6c0606e
git am security-audit/patches/*.patch
```

Option B conflicts with the unstaged changes already present in this working
tree (they contain the same edits); use it on a clean checkout. The patches
deliberately exclude the other workstream's in-flight changes (the docs batch,
`.gitignore`, `scripts/verify_deployed_sample.py`, `.playwright-mcp/`, and the
CI `prune-cache` tweak), which are preserved untouched in the working tree.
The patches are a transport artifact: once the commits are in place they can
be deleted.

## Status by item

| Item | Status | Files changed | Tests / evidence | Remaining risk |
| --- | --- | --- | --- | --- |
| S1 stop unbounded demo work | done | `backend/glide/deploy/dispatcher.py`, `backend/glide/deploy/worker.py`, `tests/unit/test_dispatcher.py`, `tests/unit/test_deploy_entrypoints.py` | `test_dispatcher_never_schedules_sample_tenants` (only live tenants enqueue); `test_deployed_sample_store_never_builds_a_bedrock_runner` (monkeypatches `build_agent_runner` to raise, then completes a sample run through the deterministic planner) | The public demo stays open, so anonymous traffic can still create durable state and on-demand deterministic runs. Bounded by the S4 throttle (1 rps / burst 5 on session creation) and the 24 h snapshot TTL; no Bedrock or Amazon Location spend is reachable from the sample path. |
| S2 secrets out of Lambda env | done | `backend/glide/deploy/secrets.py` (new), `backend/glide/api/app.py`, `backend/glide/deploy/api.py`, `backend/glide/deploy/worker.py`, `infra/template.yaml`, `scripts/validate_template.py`, `scripts/deploy.ps1`, `tests/unit/test_deploy_entrypoints.py` | Template check fails if a secret value or `resolve:secretsmanager` reference reaches a function environment; `test_lambda_app_reads_both_secrets_from_secrets_manager` builds the Lambda app with **no** `GLIDE_SESSION_SECRET`/`GOOGLE_CLIENT_SECRET` set and proves the cookie cipher key is derived from the fake Secrets Manager value; worker equivalent test | Rotation is still a human decision (below). Live check that `aws lambda get-function-configuration` no longer shows the values was not run (no AWS calls allowed). |
| S3 no full-table scan | done | `backend/glide/adapters/dynamodb.py`, `backend/glide/adapters/sqlite.py`, `tests/unit/test_session_lookup.py` | `X-Glide-Session: attacker-supplied` → HTTP 404 with **0 Scan** and exactly 1 bounded Query; known session → 0 Scans; expired snapshot → `None`; `clear_user` removes both items | Snapshots written before the change have no `SESSION#<id>` item, so their session id stops resolving for at most 24 h until the TTL expires (no backfill, as the work order allows). |
| S4 API logging + throttling | done | `infra/template.yaml`, `scripts/validate_template.py` | Validator asserts the 30-day access-log group, a JSON format containing `$context.requestId`/`status`/`path`, default 25 rps / 50 burst, and a tighter `POST /api/demo/session` (1 rps / 5 burst); all 23 non-SAM resources validated against the live CloudFormation resource specification | WAF was deliberately not added (needs a `us-east-1` CloudFront-scoped WebACL and a cost decision). `sam validate --lint` could not run here (below). |
| S5 CORS restricted | done | `backend/glide/api/app.py`, `tests/unit/test_app_config.py` | `test_production_cors_allows_only_the_configured_origin` asserts the middleware origin list is exactly `[GLIDE_FRONTEND_ORIGIN]` under `GLIDE_ENV=production`; dev keeps localhost origins | None known. |
| S6 CI supply chain | done | `.github/workflows/ci.yml`, `.github/dependabot.yml`, `tests/unit/test_ci_supply_chain.py` | Every `uses:` is a 40-hex commit (pins resolved live from the upstream repositories: `actions/checkout` v4.4.0 = `11d5960a…`, `astral-sh/setup-uv` v5.4.2 = `d4b2f3b6…`, `actions/setup-node` v4.4.0 = `49933ea5…`); workflow declares `permissions: contents: read`; test enforces both | A green CI run requires a push (not permitted here) — unverified. |
| S8 no secret on the deploy line | done | `scripts/deploy.ps1`, `infra/template.yaml`, `infra/README.md`, `tests/unit/test_deploy_script_secrets.py` | Test asserts the only `--parameter-overrides` values are `Stage`, `BedrockModelId`, `GoogleClientId`, `GoogleClientSecretArn`, `FrontendOrigin`, that the value only reaches the AWS CLI through a `file://` temp file, and that it is never echoed; script parses under the PowerShell parser; template enforces an ARN pattern; `infra/README.md` documents both supported flows (environment value or an existing secret ARN) instead of the removed `-GoogleClientSecret` parameter | The script must be run by a human with AWS credentials; no live deploy was performed. |
| S9 alarms | done | `infra/template.yaml`, `scripts/validate_template.py` | Validator asserts the SNS topic, `DlqDepthAlarm` (DLQ depth ≥ 1), `WorkerErrorsAlarm` (Lambda `Errors` ≥ 1), `Api5xxAlarm` (`5xx` ≥ 5/5 min) and `ApiThrottleAlarm` fed by a `{ $.status = "429" }` metric filter over the access logs, each notifying the topic; optional email subscription via `AlarmEmail` | HTTP APIs publish no throttle metric, so throttles are derived from access logs (documented in the template). AWS Budgets notifications at the USD 75 ceiling were **not** added: they are absent from the S9 line in the task prompt and would need a budget-definition decision. |
| S7 MCP pins and scope | done (with one blocked sub-step) | `tools/mcp/**`, `scripts/install-mcps.ps1`, `scripts/refresh-mcp-pins.ps1` (new), `docs/mcp-setup.md`, `tests/unit/test_mcp_configs.py` | `@playwright/mcp@0.0.80`, `@modelcontextprotocol/server-github@2025.4.8`, `@cocal/google-calendar-mcp@2.6.3` with refreshed lockfiles (verified against the registry); AWS servers moved to the upstream-documented `public.ecr.aws/awslabs-mcp/...` images pinned by **verified digests** (fetched by digest and content-hashed); forward `AWS_PROFILE`/`AWS_REGION` plus a read-only `~/.aws` mount instead of raw keys; docs state minimum IAM/GitHub scopes; tests fail on `latest`, on unpinned image refs, on owner paths, and on Playwright approval drift | The installed `.codex/config.toml` still says `default_tools_approval_mode = "auto"` for Playwright; `.codex/` is read-only in this sandbox, so re-run `scripts/install-mcps.ps1` from a normal terminal to regenerate it (the staged config is authoritative and says `writes`). Two supply-chain corrections were needed: `ghcr.io/awslabs/mcp/*` images do not exist publicly (404), and `@zetalytics/mcp-google-calendar` is not published on npm (404). |
| S10 session revocation | **blocked** | none | — | Product decision required (stateless 7-day sessions vs. revocable sessions). Per the work order this is recorded rather than guessed; no code changed. Options and acceptance test are in `security-audit/NEXT-STEPS.md`. |
| S11 quick hardening | done | `backend/glide/agent/host.py`, `backend/glide/agent/prompts.py`, `backend/glide/agent/strands_runner.py`, `backend/glide/api/schemas.py`, `backend/glide/api/routes/demo.py`, `backend/glide/deploy/worker.py`, `frontend/src/api.ts`, `frontend/scripts/verify-api-retry.mjs`, `frontend/e2e/api-retry.spec.ts`, `frontend/package.json`, `.github/workflows/ci.yml`, `infra/template.yaml`, `scripts/validate_template.py`, `tests/unit/test_host_validation.py`, `tests/unit/test_s11_hardening.py` | F13: parse-failure log carries only `messageId` + error type (test asserts body/receipt handle are absent). F15: retries only GET/HEAD/OPTIONS; `npm run verify:api-retry` executes the shipped module through Node's type stripping and asserts POST is attempted once while GET is retried once, CI runs it, a mutation check (guard removed) fails as expected, and a Playwright spec covers the browser path. F16: `time_zone` must be IANA and ≤ 64 chars, place labels ≤ 200, prompt interpolation collapses and caps both. F17: `RejectionCode` carries prompts/exceptions/logs while the tool response keeps the specific detail. F12/F14: route-failure logs carry a hashed place pair. F18: DynamoDB PITR enabled and validated. F20: no owner paths in published MCP configs | F15's browser-path spec cannot execute in this sandbox (Playwright forks workers), so the Node-level check is the executed evidence; the F18 restore itself is only validated by the template assertion (no AWS access). |

## Verification log (final sweep)

| Command | Result |
| --- | --- |
| `uv run pytest -q` | **300 passed** (baseline 268; 32 new tests) |
| `uv run ruff check .` | clean |
| `uv run python scripts/validate_template.py` | `infra/template.yaml: OK` (extended with S2/S4/S9/S11/F18 invariants) |
| `cd frontend; npm run typecheck` | clean |
| `cd frontend; npm run verify:api-retry` | passes — "POST attempted once, GET retried once"; with the guard removed it fails with `AssertionError: POST /api/runs must be attempted exactly once`, so the check is sensitive to the regression it guards |
| `cd frontend; npm ci --dry-run` | up to date (lockfile consistent) |
| `security-audit/apply-remediation.ps1` (scratch clone) | fetches 11 commits from the bundle and `reset --mixed` moves the branch while a deliberately modified file stays uncommitted, which is the exact hand-off behaviour the real tree needs |
| `cd frontend; npx playwright test --list` | 16 tests in 4 files, including `api-retry.spec.ts` |
| `cd frontend; npm run build` | **UNVERIFIED** — esbuild cannot spawn its service process in this sandbox (`ensureServiceIsRunning`, `EPERM`) |
| `cd frontend; npm ci` (real install) | **UNVERIFIED** — not run to avoid destroying the existing `node_modules` when sandboxed child processes are denied; `--dry-run` passed |
| `sam validate --lint` | **UNVERIFIED** — SAM CLI 1.166.1 is installed but cannot write its `%APPDATA%\AWS SAM\metadata.json` in the sandbox. Substitutes: the template parses under the repo's YAML loader; every non-SAM resource (23) validates against the live CloudFormation resource specification; `AWS::Serverless::HttpApi` properties (`AccessLogSettings`, `DefaultRouteSettings`, `RouteSettings`) were checked against the SAM schema shipped with the installed CLI |
| `npx playwright test api-retry` (execution) | **UNVERIFIED** — Playwright's runner spawns worker processes, which the sandbox denies (`EPERM`) |
| PowerShell syntax | `scripts/deploy.ps1`, `scripts/install-mcps.ps1`, `tools/mcp/aws/install-aws-mcps.ps1`, `scripts/refresh-mcp-pins.ps1` all parse under `[Parser]::ParseFile` |
| Live AWS checks (function configuration, alarms, reserved concurrency, rotation) | **NOT RUN** — the task forbids live AWS calls without explicit human approval |

Environment capability checks (retested, so the UNVERIFIED rows above are not
an oversight):

- `sam validate` was retried with `APPDATA`/`LOCALAPPDATA` pointed at a
  workspace-local directory that the shell can write to; the SAM CLI's write of
  `AWS SAM\metadata.json` is denied and it leaves behind a directory the shell
  cannot delete. The template is instead covered by the repo validator, the
  live CloudFormation resource specification, and the installed SAM schema.
- `node`'s `child_process` API is denied for every spawn (`EPERM`), including
  `exec("echo")`. That is what blocks esbuild (so `npm run build`) and
  Playwright's worker processes (so e2e execution). `npm run typecheck` passes
  because the shell, not Node, starts `tsc`.
- Writing to `.codex/` is denied by the sandbox policy (probe rejected), so the
  installed MCP config cannot be regenerated from here; the staged config is
  authoritative and the installer is the documented fix.

## Decisions taken, and decisions still needed

Taken (chosen from the options the work order allows):

1. **Public demo stays open**, but sample tenants are never scheduled and the
   deployed worker runs them on the deterministic planner. This preserves the
   submitted UX while removing the Bedrock/Location spend path. Residual
   anonymous work is bounded by the new API throttles.

   *Amended 12 September:* the "never scheduled" half changed when background
   watching became the visible product. Sample tenants are now scheduled only
   while watching and due, at a 15-minute floor, no more than three new
   sessions per dispatcher tick, and never after the 24-hour snapshot
   expires. Sample runs still use the deterministic planner, so there is
   still no Bedrock or Location spend path; what changed is that the residual
   queue and DynamoDB writes are now bounded by the interval rather than
   eliminated.
2. **No WAF** in this change set (us-east-1 resource + cost decision).
3. **No AWS Budgets resource** (not part of the S9 line in the task prompt;
   needs a budget-definition decision).
4. **S10 left unimplemented and marked blocked**, per "if the product decision
   is unclear, record it as blocked and continue rather than guessing".
5. **MCP supply chain corrected rather than guessed**: the ghcr.io images and
   the `@zetalytics` npm package named in the audit do not exist publicly, so
   the closest upstream-supported, verifiable alternatives were pinned.

Still needed from a human:

1. **Rotate the session key and Google client secret** (Decision 2 in
   `NEXT-STEPS.md`). The fix stops new deployments from exposing them, but any
   value that was already visible in a Lambda configuration should be rotated.
2. **S10 session model**: stateless 7-day sessions vs. server-side revocation.
3. **WAF and budget**: both are cost/scope decisions.
4. **Re-run `scripts/install-mcps.ps1`** in a normal terminal so the installed
   `.codex/config.toml` matches the staged config (Playwright approval mode
   `"writes"`, digest-pinned AWS images, `<USER_HOME>` mount).
5. **Push and confirm CI** for S6 (a green run is the stated acceptance).

## Scope notes

- `docs/completion-progress.md` and `docs/next-steps.md` were **not** edited:
  both carry uncommitted work from another workstream, and committing them
  would have swept that work into these commits. This report is the record of
  the remediation instead.
- The unrelated working-tree changes (docs batch, `.gitignore`,
  `scripts/verify_deployed_sample.py`, `.playwright-*` artifacts, and the CI
  `prune-cache` tweak) are untouched; the mirror commits exclude the CI tweak
  so each item commit stays scoped.
- The dead `security-audit/next-steps-briefing.wav` stub mentioned in the task
  prompt is already absent from the working tree.
- No secrets were printed, logged, or committed. Secret values resolved during
  verification were fake test values only.
