# Security Audit — 2026-09-11

> **Status, 14 September 2026.** The findings in this audit were remediated in
> the S1-S11 work order; the per-item evidence is in
> [../security-audit/REMEDIATION-REPORT.md](../security-audit/REMEDIATION-REPORT.md).
> The one open item is the WAF rate-based rule, tracked in
> [../submission/release-checklist.md](../submission/release-checklist.md).

Scope: full repository, excluding `.venv`, `node_modules`, build artifacts, and
local caches. Performed as a combination of parallel subagent scans (interrupted
early) and direct verification by the coordinating agent. Result: **no critical
vulnerabilities found, and no secrets leaked into git history or the working
tree.** The top issues are availability/cost exposure and credential-hygiene
gaps, not data-breach bugs.

## Coverage honesty

These were read line-by-line and verified directly:

- `backend/glide/api/auth.py`, `deps.py`, `app.py`, `routes/*.py`
- `backend/glide/deploy/credentials.py`, `api.py`
- `backend/glide/live/processor.py`
- `backend/glide/agent/prompts.py`, `tools.py`
- `infra/template.yaml` (full)
- `.github/workflows/ci.yml`
- `scripts/validate_template.py`
- Full git-history secrets scan + working-tree credential-pattern scan

Verified by targeted grep, not full line review:

- `backend/glide/agent/host.py` (32 KB) — server-side proposal validation
  (`accept_proposal` / `_validate_journey` / rejection loop) exists, but not
  every branch was reviewed.
- `backend/glide/adapters/dynamodb.py` and `sqlite.py` — no string-built SQL
  found, but not every query was reviewed.
- `frontend/src/*` — no XSS sinks or embedded secrets found.
- `tools/mcp/*` — config-level review only.

## Findings

### High

#### H1. Demo API is public and unauthenticated in production

- Evidence: `backend/glide/api/routes/demo.py` registers `POST /api/demo/session`
  with no auth dependency; `backend/glide/api/deps.py` accepts any
  `X-Glide-Session` header principal.
- Impact: demo tenants are isolated (`sample-*`), so there is no live-data
  exposure. But anyone can drive billed geo-places searches, Bedrock
  invocations, DynamoDB writes, and SQS jobs — a real cost/DoS vector.
- Fix: disable the demo router when `GLIDE_ENV=production`, or require
  authentication on demo endpoints in production.

#### H2. Single shared session key with wide blast radius

- Evidence: `infra/template.yaml` bakes `GLIDE_SESSION_SECRET` into the Lambda
  environment at deploy time via
  `{{resolve:secretsmanager:${SessionSecret}:SecretString:GLIDE_SESSION_SECRET}}`.
- Impact: anyone who can read function configuration or stack parameters
  obtains the key that signs/encrypts every session cookie. A forged
  `google:<sub>` session would unlock that user's stored OAuth tokens in
  Secrets Manager.
- Fix: fetch the key from Secrets Manager at runtime, rotate it, and treat
  `lambda:GetFunctionConfiguration` / `cloudformation:DescribeStacks` read
  access as sensitive.

### Medium

#### M1. Public API has no WAF, throttling, or access logging

- Evidence: `infra/template.yaml` `HttpApi` has no `AccessLogSettings` and no
  WAF association; no rate limits anywhere at the edge.
- Impact: compounds H1; unbounded public requests to billed services.
- Fix: add `AccessLogSettings`, AWS WAF rate-based rules, and API Gateway
  throttling.

#### M2. Google client secret rides through CloudFormation into Lambda env vars

- Evidence: `GoogleClientSecret` is a `NoEcho` parameter in
  `infra/template.yaml`, passed into both `ApiFunction` and `WorkerFunction`
  environment variables. The worker needs it for token refresh.
- Impact: visible to anyone with `cloudformation:DescribeStacks` or
  `lambda:GetFunctionConfiguration`.
- Fix: fetch from Secrets Manager at runtime in both functions; rotate on any
  suspected exposure.

#### M3. Dev CORS origins allowed in production

- Evidence: `backend/glide/api/app.py` always allows
  `http://localhost:5173` and `http://localhost:4173` alongside
  `GLIDE_FRONTEND_ORIGIN`.
- Fix: in production allow only `GLIDE_FRONTEND_ORIGIN`.

#### M4. MCP tooling forwards broad credentials to third-party containers

- Evidence: `tools/mcp/aws/*` forwards full AWS credentials into
  `ghcr.io/awslabs/mcp/*` Docker images; `tools/mcp/github/*` forwards a GitHub
  PAT, and its `SETUP.md` suggests `contents: read/write`.
- Impact: local-dev only, but a compromise of any container image exposes
  credentials with broad scope.
- Fix: use an SSO profile with a least-privilege role, narrow the PAT to
  read-only where possible, and pin image digests.

#### M5. CI pins actions by tag, no explicit permissions

- Evidence: `.github/workflows/ci.yml` uses `actions/checkout@v4`,
  `astral-sh/setup-uv@v5`, `actions/setup-node@v4`; no `permissions:` block.
- Fix: pin by commit SHA and add an explicit read-only `permissions:` block.

### Low

#### L1. AWS-owned KMS keys for PII

- DynamoDB holds calendar contents; Secrets Manager holds OAuth tokens. Both
  use AWS-owned keys by default. Consider customer-managed keys and enable
  point-in-time recovery on the state table.

#### L2. Dependency ranges

- Python/JS dependencies use caret ranges. Lockfiles are committed (`uv.lock`,
  `package-lock.json`), which mitigates this. Optionally enable dependency
  scanning (Dependabot/`uv audit`) in CI.

## What was verified as solid

- Secrets hygiene: `.env`, `secrets/`, `*.db`, and `private.md` are properly
  gitignored and untracked. Full history scan found no AWS keys, private keys,
  JWTs, or provider tokens.
- OAuth: PKCE with `compare_digest` state validation, scope enforcement,
  ID-token audience check, pinned redirect URI, per-user tokens in Secrets
  Manager, refresh-token revocation on logout.
- Sessions: Fernet-encrypted, `HttpOnly`, `SameSite=Lax`, `Secure` in
  production, expiry enforced on decrypt.
- Authorization: run, decision, and place endpoints are scoped to the
  authenticated user with explicit ownership checks.
- Prompt injection: event text treated as data, strict tool schemas forbid
  invented references and generic writes, server-side proposal validation with
  rejection loop, mutation guard plus settings-revision recheck before commit,
  explicit injection tests in `tests/unit/test_scheduling.py`.
- Frontend: no `dangerouslySetInnerHTML`/`innerHTML`/`eval`, no embedded
  credentials, React only as a runtime dependency.
- Infra: S3 SSE + full public-access blocks, CloudFront OAC pinned by source
  ARN, HTTPS-only, KMS-encrypted SQS, scoped IAM (specific table/queue/secret
  ARNs and Bedrock model), 30-day log retention.

## Suggested remediation order

1. H1 — gate demo routes behind `GLIDE_ENV` (small change, biggest risk).
2. M1 — add WAF/throttling/access logging to the API.
3. H2 — runtime Secrets Manager lookup for the session key.
4. M2 + M3 — runtime secret lookup; prod-only CORS origins.
5. M4 + M5 + L1 + L2 — hardening tasks, safe to parallelize after 1–4.

## Verification checklist for fixes

- `uv run pytest -q` and `uv run ruff check .` pass after each change.
- `uv run python scripts/validate_template.py` passes after template edits.
- For H1: demo endpoints return 404/403 with `GLIDE_ENV=production`.
- For H2/M2: confirm `GLIDE_SESSION_SECRET` and `GOOGLE_CLIENT_SECRET` no
  longer appear in Lambda environment configuration.
- For M1: confirm API access logs flow to CloudWatch and a WAF rate rule is
  attached to the HTTP API.

## Continuation — 2026-09-12

Read-only second pass. No source, config, or git state was changed; the only
edit is this appended section. Everything below was read line-by-line in the
working tree (last code commit `6c0606e` plus uncommitted documentation
changes). `git log` shows no remediation commits for the items below, and
`infra/template.yaml` has no uncommitted changes, so nothing was re-verified as
fixed — it was verified as still present. No new Critical issue was found.

### New findings

#### N1. Unauthenticated full-table scan on every sample-session request (Medium)

- Evidence: `backend/glide/api/deps.py:69-76` accepts any `X-Glide-Session`
  value and resolves it through `DemoSessionStore.get()`;
  `backend/glide/api/demo_store.py:154-161` and `:201-207` fall through to
  `get_sample_snapshot_by_session`; `backend/glide/adapters/dynamodb.py:375-382`
  iterates `self._scan()`, and `_scan()` (`:121-132`) pages through the whole
  table with no `FilterExpression` and no projection.
- Reproduced locally without AWS: with `GLIDE_ENV=production`,
  `create_app(state_store=DynamoDbStateStore(fake_client, "glide"))` plus
  `GET /api/day` with `X-Glide-Session: guessed-session-id` returned `404` and
  the fake client recorded exactly 1 `Scan` and 0 `Query`.
- Impact: one full-table read per request, for anonymous guesses and for
  legitimate sample traffic alike. The table holds every tenant's runs, plans,
  decisions, receipts and blocks, so cost and latency scale with total tenant
  data; the 30-second API Lambda timeout bounds one request, not the rate. This
  compounds H1 (which is what makes the endpoint reachable at all).
- Fix: store the session under its own partition key (`SESSION#<id>`) or add a
  `session_id` GSI and query it instead of scanning.

#### N2. Settings and place text are interpolated straight into the agent prompt (Low)

- Evidence: `backend/glide/api/schemas.py:84` (`time_zone: str | None`, no
  bound or allowlist) → `backend/glide/api/routes/demo.py:366-367` →
  `backend/glide/agent/prompts.py:53`; `start_address` at `prompts.py:56` comes
  from `settings.start_place.label` (`backend/glide/agent/strands_runner.py:368-370`),
  which is also unbounded.
- Impact: self-scoped prompt-injection surface only — a user can inject text
  into their own run, and every proposal is re-validated in
  `ToolHost.accept_proposal`, so no cross-tenant or write-authority effect was
  found. `time_zone` is never used as a `ZoneInfo` key, so it cannot crash a run.
- Fix: validate `time_zone` against `ZoneInfo`/the IANA list and cap the length
  of `time_zone` and place labels.

#### N3. Model-controlled strings are echoed into the repair prompt (Low)

- Evidence: `backend/glide/agent/strands_runner.py:379` passes
  `host.last_rejection` to `build_repair_prompt`; those rejections embed
  model-supplied values at `backend/glide/agent/host.py:519`, `:544` and `:607`;
  `backend/glide/agent/prompts.py:64-70`.
- Impact: the model's own text is replayed to the same model inside the same
  run. No privilege change (validation still gates every write); recorded for
  completeness only.
- Fix: map rejections to a fixed reason-code enum before echoing them.

#### N4. Frontend retries non-idempotent POSTs (Low)

- Evidence: `frontend/src/api.ts:31-40` retries once on any transport error for
  every verb; `backend/glide/api/routes/demo.py:150-165` creates a new run row
  and queue message per POST.
- Impact: a response lost after the server processed the request queues a
  second run — duplicate Bedrock/Amazon Location spend and duplicate receipts.
- Fix: retry only idempotent verbs, or honour a client-generated idempotency
  key on `/api/runs`.

#### N5. Deploy script puts the Google client secret on the command line (Low)

- Evidence: `scripts/deploy.ps1:23` (mandatory `-GoogleClientSecret`) and
  `:57-62` (`sam deploy --parameter-overrides ... "GoogleClientSecret=$GoogleClientSecret"`).
- Impact: the value appears in shell history, in the SAM CLI process command
  line, and in any wrapper logs, in addition to the CloudFormation stack
  parameter (NoEcho masks display only, not storage).
- Fix: keep the secret in Secrets Manager/SSM and pass only its name/ARN, as
  `SessionSecret` already does; or read it from the environment inside the
  script rather than from a parameter.

#### N6. Playwright MCP is auto-approved and can drive the local API's credentials (Low, needs confirmation)

- Evidence: `tools/mcp/playwright/mcp-section.toml:11`
  (`default_tools_approval_mode = "auto"`); the local API holds live Google
  tokens via `scripts/load_env.ps1:90-103`; the dev server proxies `/api` to it
  (`frontend/vite.config.ts:7-12`).
- Impact: browser automation runs without prompting and can reach the dev
  server/API that is holding live credentials. Whether the MCP browser keeps a
  persistent profile (and therefore the Google session cookie) is
  version-dependent — needs confirmation.
- Fix: set Playwright to `writes`/`ask` approval while live credentials are
  loaded.

#### N7. MCP dependency hygiene (Low)

- Evidence: `tools/mcp/github/package.json:23`,
  `tools/mcp/playwright/package.json:26` and
  `tools/mcp/google-calendar/package.json:23` all depend on `"latest"`;
  `scripts/install-mcps.ps1:118` installs with `--no-audit`;
  `tools/mcp/github/package-lock.json:24-25` pins
  `@modelcontextprotocol/server-github@2025.4.8`, an archived upstream package;
  `tools/mcp/aws/install-aws-mcps.ps1:5-9` and `:17-19` pull images by mutable
  tag with no digest.
- Impact: local-development only, but a fresh install silently accepts whatever
  is published, skips advisory checks, and forwards credentials to an
  unmaintained server.
- Fix: pin exact versions and use `npm ci`, run `npm audit`, prefer the
  maintained `github/github-mcp-server`, and pin containers by digest.

### Corrections and additions to the original report

- L2 wording: Python dependencies use open-ended minimum ranges
  (`pyproject.toml:8-21`), not caret ranges; JS uses caret ranges
  (`frontend/package.json:25-32`). The mitigation the report names (committed
  lockfiles) still stands, and `npm audit --audit-level=high` in `frontend/`
  reported "found 0 vulnerabilities" on 2026-09-11.
- M4 scope is slightly wider than described: Playwright is configured with auto
  approval (`tools/mcp/playwright/mcp-section.toml:11`), and the calendar server
  is a third-party package (`@zetalytics/mcp-google-calendar`) holding a
  service-account key with calendar write access
  (`tools/mcp/google-calendar/package.json:23`, `.../SETUP.md:42-51`).
- "Sessions: ... Secure in production" (What was verified as solid) is
  confirmed with evidence: `infra/template.yaml:225` sets
  `GLIDE_SECURE_COOKIES: "true"`, applied by `backend/glide/api/app.py:126-135`
  and `backend/glide/api/auth.py:267-279`.
- No other baseline claim was contradicted, and the "secrets hygiene" section
  still holds: the working-tree credential-pattern scan found only the
  placeholder `github_pat_...` in `tools/mcp/github/SETUP.md:22`, and `.env`,
  `secrets/`, `private.md` and `*.db` remain untracked and gitignored.

### Item-by-item confirmation (scope 1-9)

1. `agent/host.py` + `agent/strands_runner.py` — **Confirmed.** `run_id` is
   server-bound (`host.py:184-185`, `:457-458`); proposal keys/occurrence ids
   must match the server-supplied pair set (`:462-505`); CREATE/REMOVE/DECISION
   reason-code rules, route-reference identity and deterministic feasibility are
   enforced (`:512-607`); the model's own `proposed_start/proposed_end`
   (`tools.py:144-145`) are never used — write times come from
   `evaluate_journey_candidate` (`host.py:663-668`); budgets are 16 turns
   (`strands_runner.py:60`), 200 s deadline (`:61`, `:350-353`), 30 route calls
   (`host.py:317-325`) and 20 events / 10 journeys (`:97-99`), with 24
   validation tests in `tests/unit/test_host_validation.py`. Gap: `lookup_place`
   has no per-run counter (`host.py:226-256`) but is turn-bounded. The mutation
   guard runs immediately before and after every provider write with
   compensating rollback (`domain/live.py:447-502`), and the settings-revision
   recheck runs before commit (`live/processor.py:118`, `:134-142`).
2. `adapters/dynamodb.py` + `adapters/sqlite.py` — **Confirmed parameterized.**
   DynamoDB always uses `ExpressionAttributeValues` (e.g. `:273-275`, `:291-299`);
   SQLite always uses `?` placeholders (e.g. `sqlite.py:107-113`, `:211-221`);
   no string interpolation into expressions or SQL; TTL set on receipts and
   snapshots (`dynamodb.py:215-217`, `:237`); keys are tenant-scoped. The single
   cross-tenant reader is `_scan()` / `get_receipts(run_id=None)`
   (`dynamodb.py:121-132`, `:316-327`) — only tests call it without a run id,
   but the scan backs N1.
3. `adapters/google_calendar.py` — **Confirmed.** Tokens are never logged;
   failures become `ProviderUnavailableError(str(exc))` (`:260-262`, `:281-284`,
   `:328-331`, `:349-354`) carrying HTTP status/URL/response body, not
   credentials; deterministic ids make inserts idempotent on 409 (`:252-276`);
   Glide-owned events are excluded from source reads (`:210-216`); event titles
   and locations reach the model only as typed `ScheduleEvent` fields, never as
   prompt text (the interpolation risk is N2's `time_zone`/`start_address`).
4. `jobs/*` — **Confirmed.** Payloads carry only `user_id`/`trigger`/`run_id`
   (`sqs_queue.py:164-175`) and the worker treats them as opaque data
   (`deploy/worker.py:138-150`); FIFO with `maxReceiveCount: 3` and a DLQ
   (`infra/template.yaml:159-175`); timing is ordered 200 s agent deadline <
   240 s worker timeout < 300 s visibility (`template.yaml:277`, `:268`, `:171`).
   No poison-message loop found. Not present: a DLQ depth alarm.
5. Frontend + configs — **Confirmed clean.** No
   `dangerouslySetInnerHTML`/`innerHTML`/`eval`/`document.write` anywhere in
   `frontend/src`, `index.html` or `e2e`; every user/calendar/model string
   renders as JSX text (`App.tsx:320-343`, `:416-428`); the only storage is the
   sample session id (`api.ts:52-65`); the session cookie is HttpOnly and never
   read by JS; the one external link uses `rel="noreferrer"` (`App.tsx:521`);
   the Vite proxy is dev-only (`vite.config.ts:7-12`). Note:
   `trace: "retain-on-failure"` (`playwright.config.ts:10`) can capture cookies
   and headers into the gitignored `test-results/`.
6. `tools/mcp/*` — **Confirmed with additions** (see N6, N7). Full AWS
   credential sets are forwarded into the containers
   (`tools/mcp/aws/mcp-section.toml:24-27` and parallels), a PAT with
   `contents: read/write` is suggested (`tools/mcp/github/SETUP.md:53-55`), the
   service-account key path lives in-repo and is gitignored
   (`.gitignore:29-33`), and every npm spec is unpinned.
7. `scripts/*.ps1` + `live_smoke.py` — **Confirmed**, with N5. No secret is
   printed; `.env` writing rejects newline injection
   (`import_google_oauth.ps1:35-37`); env loading validates variable names
   (`load_env.ps1:95-97`); no hard-coded account ids (the `eu-west-1` default
   matches the deployed stack); `live_smoke.py:1-11` prints redacted evidence.
8. Dependency audit — **Partial.** `npm audit --audit-level=high` in
   `frontend/`: 0 vulnerabilities. Locked versions are current (starlette
   1.6.0, fastapi 0.141.1, cryptography 50.0.1, urllib3 2.7.0, requests 2.34.2,
   vite 7.3.6, esbuild 0.28.2, react 19.2.8). `pip-audit` could not run: PyPI
   is unreachable from this sandbox (uvx fetch failed with a tunnel/refused
   connection), this uv has no `audit` subcommand, and there is no Dependabot
   config (`.github/` contains only `workflows/ci.yml`). Unusual entries:
   `httpx2==2.12.0` and `httpx2-jsfetch==1.0` (`uv.lock:764-787`), pulled in by
   `mcp` (`uv.lock:861`), a transitive dependency of `strands-agents`
   (`uv.lock:1583`).
9. H1/H2 plus M1-M5/L1-L2 — see the table below. Both High findings remain
   reachable exactly as described.

### Status of the original items

| Item | Status | Evidence |
| --- | --- | --- |
| H1 demo API public in production | **Open** | `backend/glide/api/app.py:195` includes `demo.router` unconditionally; reproduced with `GLIDE_ENV=production`: `create_app(...)` exposes `/api/demo/session`, `/api/demo/reset`, `/api/demo/events/{occurrence_id}` (20 paths total). `backend/glide/deploy/api.py:35` is the Lambda's app factory. |
| H2 session key in Lambda env | **Open** | `infra/template.yaml:226-227` still resolves `SessionSecret` into `GLIDE_SESSION_SECRET`; the only Secrets Manager use in code is per-user tokens (`backend/glide/deploy/api.py:28-32`); `app.py:126-131` reads `os.getenv` only. `lambda:GetFunctionConfiguration` returns the resolved value. |
| M1 no WAF/throttling/access logs | **Open** | `infra/template.yaml:205-208`: `HttpApi` sets only `StageName`; no `AccessLogSettings`, no `DefaultRouteSettings`, no `AWS::WAFv2` resource anywhere; `scripts/validate_template.py` checks none of these. Uncommitted `docs/next-steps.md` diffs plan this as "N9". |
| M2 Google secret in function env | **Open** | `infra/template.yaml:229` (api) and `:281` (worker). |
| M3 dev CORS origins in production | **Open** | `backend/glide/api/app.py:182-192` always allows `http://localhost:5173` and `http://localhost:4173`. |
| M4 broad MCP credentials | **Open** | N6/N7 plus `tools/mcp/aws/SETUP.md:45-59` and `tools/mcp/github/SETUP.md:53-55`. |
| M5 CI actions by tag, no permissions | **Open** | `.github/workflows/ci.yml:11-12`, `:26-27`, `:39-43`; no `permissions:` key. |
| L1 AWS-owned keys, no PITR | **Open** | `infra/template.yaml:124-155` (no `SSESpecification`, no `PointInTimeRecoverySpecification`), `:42-45` (S3 AES256), `:164`/`:172` (SQS `alias/aws/sqs`). |
| L2 dependency hygiene/scanning | **Open (partially mitigated)** | Lockfiles committed and the frontend npm audit is clean, but no Dependabot/CI advisory step, and the Python side is unaudited (item 8). |

### Still not verified

- Real provider behaviour under hostile or oversized inputs (Bedrock, Amazon
  Location) — no AWS calls were made in this pass.
- The live Google OAuth flow end-to-end, which needs the owner's browser
  consent.
- Whether CloudFormation's `{{resolve:secretsmanager:...}}` value is redacted in
  `GetFunctionConfiguration` in this specific account (documented behaviour, not
  observed here).
- DynamoDB behaviour at scale: the transaction item cap
  (`adapters/dynamodb.py:478-482`), and the 400 KB item limit versus unbounded
  model-supplied `evidence` strings.
- Python advisory coverage (`pip-audit`/OSV unreachable) and `npm audit` for
  `tools/mcp/*` (only `frontend/` was audited).
- Whether the Playwright MCP browser keeps a persistent profile (N6 impact).
- Deployed-account configuration: WAF, API access logs, SNS/CW alarms, budgets
  (template review only).

## Continuation — post-remediation verification (2026-09-11, evening)

Second continuation pass. Unlike the previous continuation, this one ran after
the remediation commits, so its job is different: verify the S1–S11 fixes in
the tree as it stands, rather than re-examine an unremediated tree. Read-only:
no source, config, or git state was changed apart from appending this section.
Evidence is from the working tree on top of `dfc6849` (uncommitted agent-loop
and frontend edits included), a local pytest/ruff/template run, `npm audit`
runs, and in-process probes that touched no external service. No AWS or Google
call was made.

### What the remediation changed

All eleven work items in `security-audit/NEXT-STEPS.md` are present as commits
(`57f1455` S1 â€¦ `4b53e94` S11). Two High findings are closed, and the Medium/Low
list is either fixed or consciously deferred. The public repository snapshot
(`e6fcc071` per `docs/completion-progress.md`) plus `2077f6e`/`7e0fb67`/`dfc6849`
is not the same tree as this working copy: the agent-loop and frontend edits
described below are uncommitted here.

### New findings

#### N8. Places resolved through `lookup_place` can never be accepted in a proposal (Low; blocks the intended live-agent fix)

- Evidence: the uncommitted agent-loop change adds `looked_up_places` to the
  reference set used by `estimate_journey` (`backend/glide/agent/host.py:188-198`
  feeds `:347`) but not to `place_for` (`:201-204`), which is what proposal
  validation uses (`:563-564`). A CREATE journey whose route estimate references
  a looked-up place therefore fails at `:580` with "route estimate places do not
  match this journey"; with an unresolved place it fails earlier at `:572`.
- Reproduced in-process, no network: a host with a provider that resolves two
  calendar locations returned a route estimate for the looked-up ids, yet
  `accept_proposal` rejected the CREATE journey as `invalid_journey`.
- Impact: correctness/reliability, not privilege. Writes still cannot escape
  validation, but a repaired proposal that uses provider-resolved places is
  rejected. This is the same class of failure as the deployed
  `AgentProposalMissing` logs (`docs/completion-progress.md`, "Live run, first
  attempt"), so part of the deliberately deferred redeploy would still fail if
  the model resolves a place that was not already in `place_index`.
- Confidence: the rejection is proven; that it is the dominant live failure
  cause is inferred and needs confirmation against a fresh deployed run.
- One-line fix: make `place_for` also consult `self.looked_up_places`, or have
  `_validate_journey` read the same union `estimate_journey` uses.

#### N9. The installed MCP configuration has drifted from the hardened source (Low, local tooling)

- Evidence: the repo's canonical configs set Playwright to
  `default_tools_approval_mode = "writes"` (`tools/mcp/playwright/mcp-section.toml:11`,
  `tools/mcp/codex-config.toml:21`), but the installed, gitignored
  `.codex/config.toml:14` sets `"auto"` and adds extra per-tool entries. The
  installed file was modified after the staged section (21:44 vs 19:32 on
  11 September), so it is not a copy of the hardened source.
- Impact: local only, but Playwright MCP can drive the local dev server whose
  API holds live Google credentials through `scripts/load_env.ps1`
  (`frontend/vite.config.ts:7-12`). Auto-approval removes the prompt that N6
  asked for. Because `.codex/` is gitignored, this affects this machine, not
  the published repository.
- One-line fix: re-run `scripts/install-mcps.ps1` (or edit the installed copy)
  so Playwright's default approval mode matches the repo.

#### N10. S9's spend guard is still missing, and no per-route throttle covers the demo endpoint (Low; residual of M1/S4/S9)

- Evidence: `infra/template.yaml` contains no `AWS::Budgets::Budget` resource and
  `scripts/validate_template.py` checks none; the throttling that landed is the
  stage default only (`infra/template.yaml:251-253`), with the reason recorded
  at `:254-257`. `scripts/validate_template.py:125-139` actively rejects
  endpoint-keyed `RouteSettings`.
- Impact: `POST /api/demo/session` stays reachable unauthenticated, and the only
  rate control is 25 req/s / 50 burst for the whole API. Per-request cost is now
  near-zero (deterministic planner, no Bedrock or Amazon Location), so this is a
  tenant-churn and DynamoDB-cost issue bounded by the 24-hour TTL, not the
  original cost run-away. No WAF, by the S4 decision, and no automated spend
  alarm at the documented USD 75 ceiling (`docs/setup.md:156`).
- One-line fix: add an `AWS::Budgets::Budget` with an alarm action (a separate
  stack is acceptable) and assert it in the template checker.

#### N11. Cookies captured before logout remain valid (Low; S10 consciously deferred)

- Evidence: `POST /api/auth/logout` clears the cookie client-side
  (`backend/glide/api/auth.py:516-517`) and revokes the Google grant, but the
  session token is still a self-contained encrypted cookie with an expiry and
  no server-side version or revocation record (`glide/api/auth.py` cipher
  checks in `tests/unit/test_auth.py:446`). No `session_version`/`revoked_at`
  exists in the adapters.
- Impact: a stolen session cookie stays usable for its remaining lifetime (up to
  seven days) even after the user disconnects. This is the accepted risk S10
  listed as a human decision; it is recorded here so the decision is explicit
  rather than implied by omission.
- One-line fix: if revocation is wanted, store a per-user session version and
  bump it on logout so old cookies fail on decrypt.

### Corrections and additions to the earlier passes

- The previous continuation ("Continuation — 2026-09-12") described the tree at
  `6c0606e` with no remediation commits. That is now superseded: every item it
  listed as open (N1–N5, N7) has a fix in the commits above, and the two High
  findings are closed. The earlier section is left intact as the historical
  record.
- Original L2 wording is still right, and the dependency picture is unchanged:
  Python uses open-ended minimums (`pyproject.toml:8-21`), JS uses caret ranges
  (`frontend/package.json:17-26`), and lockfiles pin the resolved versions.
- M1 is now partly fixed, not open: access logging, default throttling, and four
  CloudWatch alarms with an SNS topic exist (`infra/template.yaml:247-253`,
  `:416-503`), and `scripts/validate_template.py` asserts each of them. WAF is
  still absent, as S4 explicitly deferred it.
- The claim "no AWS-owned-key change" from L1 now needs splitting: DynamoDB
  point-in-time recovery is enabled (`infra/template.yaml:186-187`) but the
  table, S3 bucket, and SQS queues still use default (AWS-owned) keys. That is a
  cost decision, not an oversight, and is recorded as open.

### Item-by-item re-confirmation (scope 1–9, current tree)

1. `agent/host.py` + `agent/strands_runner.py` — **Confirmed with one new defect
   (N8).** Server-bound `run_id`, server-supplied pair identity, action/
   reason-code rules, route-reference identity, deterministic feasibility, the
   16-turn/200-second budgets, and the post-acceptance mutation guard all still
   hold; the turn budget in the template now matches the runner
   (`DEFAULT_LIMITS` "turns": 24, asserted by `scripts/validate_template.py`).
   The new `place_for`/`known_place_refs` inconsistency is the exception.
2. `adapters/dynamodb.py` + `sqlite.py` — **Confirmed.** Every DynamoDB
   expression is literal with `ExpressionAttributeValues` (`:104-110`, `:156`,
   `:287-392`, `:468`); every SQLite statement uses `?` placeholders
   (`sqlite.py:98-113`, `:214-221`, `:266-276`); TTL is set on receipts and
   snapshots; `_scan` is reached only from `get_sample_snapshot`/receipt export,
   not from request-path session lookup, which is now a bounded `pk = :pk`
   query (`dynamodb.py:390-398`).
3. `adapters/google_calendar.py` — **Confirmed.** Credentials are never logged;
   provider failures become `ProviderUnavailableError(str(exc))` (`:262`, `:284`,
   `:331`, `:354`) carrying status/URL, not tokens; deterministic ids keep
   inserts idempotent; event text reaches the model only as typed field values.
4. `jobs/*` — **Confirmed.** Payloads still carry only `user_id`/`trigger`/
   `run_id`; the worker logs only `messageId` and the exception type on an
   unreadable body (`deploy/worker.py:132-141`); FIFO with `maxReceiveCount: 3`,
   DLQ, 240-second worker timeout, and `ScalingConfig.MaximumConcurrency: 2`
   (`infra/template.yaml:160-180`, `:319`, `:380`).
5. Frontend + configs — **Confirmed clean.** No `dangerouslySetInnerHTML`,
   `innerHTML`, `eval`, or `document.write` in `frontend/src`; the only browser
   storage is the sample session id and a local hint (`api.ts:58-71`,
   `App.tsx:50`); the single external link keeps `rel="noreferrer"`
   (`App.tsx:576-577`); the Vite proxy is dev-only; session cookies are
   HttpOnly and never read by JS.
6. `tools/mcp/*` — **Confirmed in-repo, with N9 on the installed copy.** No
   `latest` spec remains (npm servers use exact versions and lockfiles, AWS uses
   `mcp-proxy-for-aws-cli@1.6.6`), credentials are forwarded narrowly
   (`GITHUB_PERSONAL_ACCESS_TOKEN`, `AWS_PROFILE`/`AWS_REGION`), and the
   installers reject unpinned references (`scripts/install-mcps.ps1:91-96`).
   The installed `.codex/config.toml` is the one exception.
7. `scripts/*.ps1` — **Confirmed.** The deploy script reads secrets via
   `Read-Host -AsSecureString` or the environment and writes them to a
   short-lived temp file passed with `file://` so no value reaches a command
   line (`scripts/deploy.ps1:76-124`); `sam deploy` receives only the ARN
   (`:150`); no account id or credential is hard-coded; nothing echoes the
   value.
8. Dependency audit — **Partial, unchanged in substance.** `npm audit
   --audit-level=high --offline` reports 0 vulnerabilities in `frontend/` and
   in all three `tools/mcp/*` packages. Python advisories still cannot be
   checked here: this sandbox has no PyPI/OSV reachability and this `uv` has no
   `audit` subcommand. Locked versions are current (starlette 1.6.0, fastapi
   0.141.1, cryptography 50.0.1, urllib3 2.7.0, requests 2.34.2, vite 7.3.6,
   esbuild 0.28.2, react 19.2.8). The unusual transitive `httpx2==2.12.0` /
   `mcp 2.1.1` entry is still present and unverified.
9. H1/H2 plus M1–M5/L1–L2 — see the status table below. Both High findings are
   closed in the tree; H1's route is still public by design but no longer
   materializes into billed model work.

### Status of the original items (verified against this tree)

| Item | Status | Evidence |
| --- | --- | --- |
| H1 demo API public in production | **Fixed (cost vector removed; route still public)** | `POST /api/demo/session` still returns 201 under `GLIDE_ENV=production` (local probe), and the deployed worker always builds samples with `DeterministicAgentRunner` (`deploy/worker.py:77-96`), so no sample can reach Bedrock or Amazon Location. Amended 12 September: samples are now scheduled while watching, bounded to a 15-minute floor, three new sessions per dispatcher tick, and the 24-hour snapshot lifetime; the residual is queue/DynamoDB writes, not model spend. Residual: N10. |
| H2 session key in Lambda env | **Fixed** | `infra/template.yaml:275` keeps only `GLIDE_SESSION_SECRET_ARN`; `deploy/api.py:33-38` and `deploy/secrets.py` resolve the value at cold start; the template no longer contains a `{{resolve:secretsmanager:...}}` environment value. |
| M1 no WAF/throttling/access logs | **Partly fixed** | Access logs + default throttling at `infra/template.yaml:247-253`; four alarms and an SNS topic at `:416-503`; WAF intentionally deferred (S4), N10 covers the rest. |
| M2 Google secret in function env | **Fixed** | Only `GOOGLE_CLIENT_SECRET_ARN` is set (`:277`, `:332`); both functions call `resolve_secret_string`. |
| M3 dev CORS origins in production | **Fixed** | `api/app.py:190-200` adds localhost origins only when `GLIDE_ENV != production`. |
| M4 broad MCP credentials | **Fixed in-repo (N9 on this machine)** | Exact npm pins + lockfiles, AWS via a pinned proxy with `AWS_PROFILE`/`AWS_REGION` only, read-only PAT guidance, Playwright set to `writes` in the repo. |
| M5 CI actions by tag, no permissions | **Fixed** | `.github/workflows/ci.yml:9-10` `permissions: contents: read`; all actions pinned to SHAs (`:16-17`, `:35`, `:49-53`); Dependabot keeps the pins (`:github/dependabot.yml`). |
| L1 AWS-owned keys, no PITR | **Partly fixed** | PITR enabled (`infra/template.yaml:186-187`); CMKs still not used for the table, S3, or SQS. |
| L2 dependency hygiene/scanning | **Partly fixed** | Dependabot covers npm and GitHub Actions (`.github/dependabot.yml`); frontend and MCP npm audits are clean; Python advisories remain unverified (item 8). |
| S10 session revocation | **Deferred (accepted risk)** | No server-side version/revocation; see N11. |

### Status of the second-pass findings (N1–N7)

| Item | Status | Evidence |
| --- | --- | --- |
| N1 full-table scan per session request | **Fixed** | `dynamodb.py:390-398` queries `pk = SESSION#<id>`; `sqlite.py:273-278` indexes `session_id`. |
| N2 unbounded `time_zone`/place text | **Fixed** | `api/schemas.py:113-123` validates `time_zone` with `ZoneInfo`; place labels are capped in `api/routes/demo.py:46-66`. |
| N3 model text echoed into the repair prompt | **Fixed** | Rejections now carry a `RejectionCode` enum (`agent/host.py:89`, `:499-511`). |
| N4 frontend retried non-idempotent POSTs | **Fixed** | `frontend/src/api.ts:19-45` retries only GET/HEAD/OPTIONS. |
| N5 Google secret on the deploy command line | **Fixed** | `scripts/deploy.ps1:58-124`, `:150`. |
| N6 Playwright MCP auto-approval | **Open locally** | Repo config is `writes`; installed `.codex/config.toml:14` is `auto` (N9). |
| N7 MCP dependency hygiene | **Fixed in-repo** | Exact versions + lockfiles; AWS pinned to `mcp-proxy-for-aws-cli@1.6.6`; installers reject mutable references. |

### Verification runs performed in this pass

- `pytest`: 329 passed (`.venv`, current working tree).
- `ruff check .`: clean.
- `scripts/validate_template.py`: `infra/template.yaml: OK`.
- `npm audit --audit-level=high --offline`: 0 vulnerabilities in `frontend/`,
  `tools/mcp/playwright`, `tools/mcp/github`, `tools/mcp/google-calendar`.
- Local probes: production demo session still 201; guessed session rejected;
  looked-up-place proposal rejected at `accept_proposal` (N8).

### Still not verified

- Whether N8 is the dominant cause of the deployed `AgentProposalMissing` runs —
  needs one live run against the redeployed stack after the agent-loop changes
  are committed and deployed.
- Live AWS state: CloudWatch alarms, API access logs actually arriving, SNS
  subscription confirmation, and DynamoDB PITR, all read from the template only.
- Real provider behaviour under hostile inputs (Bedrock, Amazon Location), and
  the live Google OAuth path end-to-end.
- Python advisory coverage; `uv audit` is unavailable here and PyPI/OSV is
  unreachable. Locked versions were compared to the previous pass only.
- `npm audit` in online mode (all runs here used the offline advisory cache).
- CloudFormation/SAM deployability of the current template: `sam validate
  --lint` cannot run in this sandbox (`%APPDATA%` is outside the writable
  roots), and no deploy was performed.
