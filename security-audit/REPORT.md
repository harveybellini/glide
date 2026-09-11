# Glide — Read-only Security Audit Report

**Date:** 2026-09-11 (Europe/London)
**Repository snapshot:** `main` @ `6c0606e` with a dirty working tree (16 tracked
modifications that predate this audit, plus untracked `tools/`, `prompts/`,
`.codex/`, and docs).
**Mode:** Read-only. No fixes applied. No network used. Findings below were
verified against the files at the snapshot above; line numbers are exact at
that snapshot.

## Scope and method

Seven parallel area scans were requested. Each area was assigned to a separate
read-only explorer subagent with the same hard rules (no writes, no
state-changing git, no network, no secret values printed). Area ownership and
delivery are recorded honestly in "Coverage and method notes" below, because
several agent handoffs failed at the runtime level and the coordinator
completed or cross-checked some areas directly.

| # | Area | Primary source | Status |
| - | ---- | -------------- | ------ |
| 1 | Hardcoded secrets / credential handling | subagent + coordinator | complete |
| 2 | Authentication and authorization | coordinator (subagent handoff failed) | complete |
| 3 | Injection (SQL/shell/template/path) | subagent + coordinator | complete |
| 4 | Dependency and supply-chain risk | coordinator + sibling-agent evidence | partial (offline CVE matching impossible) |
| 5 | SSRF / deserialization / file parsing | subagent (off-brief) + coordinator | complete for code sinks; no uploads exist |
| 6 | CI/CD, IaC and cloud config exposure | coordinator (subagent handoff failed) | complete for repo; live cloud state unverified |
| 7 | Error handling and logging leaks | subagent | complete |

Severity scale: **Critical** (direct compromise of data/credentials),
**High** (practical abuse with significant impact), **Medium** (real but
bounded or privilege-gated impact), **Low** (hygiene/hardening). Confidence is
about the evidence, not the severity.

**No Critical findings were identified.**

## Findings, ranked by severity

### High

#### F1. Unauthenticated demo API is reachable in production and drives billed services

- **Area:** 2 (authz) / 6 (cloud exposure). **Confidence:** High (code path);
  the deployed-stack liveness claim is unverified offline.
- **Evidence:** `backend/glide/deploy/api.py:35-41` — the Lambda's app factory
  calls `create_app()`; `backend/glide/api/app.py:195` — `create_app` registers
  `demo.router` unconditionally; `backend/glide/api/app.py:250` — `GLIDE_ENV`
  only nulls the module-level `app`, it does not gate the router;
  `infra/template.yaml:220` sets `GLIDE_ENV=production` for the API function;
  `backend/glide/api/routes/demo.py:74-89` — `POST /api/demo/session` has no
  auth dependency and persists settings into the real DynamoDB table;
  `backend/glide/api/routes/demo.py:150-165` — `POST /api/runs` enqueues real
  SQS work for any sample tenant.
- **Amplification:** `backend/glide/adapters/fixtures.py:250-261` creates sample
  tenants with `enabled: True`; `backend/glide/deploy/dispatcher.py:74-103`
  scans enabled settings every 5 minutes and enqueues a run per tenant while a
  sample snapshot exists (`infra/template.yaml:330-333`; snapshot TTL is 24 h,
  `backend/glide/api/demo_store.py:97`). `infra/template.yaml:276` sets
  `GLIDE_AGENT_MODE=bedrock` and `backend/glide/deploy/worker.py:36-40` builds
  the sample processor with `build_agent_runner()`
  (`backend/glide/agent/strands_runner.py:451-453`), so anonymous sample runs
  consume Bedrock (and Amazon Location where routed).
- **Exploit path:** anonymous `POST /api/demo/session` → repeat N times →
  each session self-schedules a Bedrock-backed run every 5 minutes for up to
  24 h, plus attacker-triggered runs on demand. Impact is unsolicited writes,
  queue growth, and unbounded model/geo spend (the project's own USD 75 ceiling
  is documented in `docs/next-steps.md`).
- **Notes:** demo tenants are isolated (`sample-` users, per-session UUIDs), so
  no cross-tenant read was found. This is a cost/abuse exposure, not a data
  breach. Prior reports tracked this as H1 and partly as N9.

#### F2. Long-lived secrets are resolved into Lambda environment variables at deploy time

- **Area:** 1 (credential handling) / 6 (IaC). **Confidence:** High on the
  mechanism; the set of principals holding config-read permissions is
  unverified offline.
- **Evidence:** `infra/template.yaml:226-227` — `GLIDE_SESSION_SECRET` is a
  CloudFormation dynamic reference
  (`{{resolve:secretsmanager:${SessionSecret}:SecretString:GLIDE_SESSION_SECRET}}`)
  baked into `ApiFunction` environment; `backend/glide/api/app.py:126-135`
  reads it with `os.getenv`. `infra/template.yaml:229` and `:281` put
  `GOOGLE_CLIENT_SECRET` into both API and Worker environments. No code fetches
  the session key from Secrets Manager at runtime.
- **Exploit path:** anyone able to read Lambda function configuration
  (`lambda:GetFunctionConfiguration`) or otherwise inspect the deployed
  environment obtains the session-encryption key and the Google client secret.
  With the session key, an attacker can mint a valid `AuthSession` for any
  `google:<sub>` identity (`backend/glide/api/auth.py:224-243`, `:262-288`) and
  act as that user, including reading their calendar through the API and
  revoking/using stored grants through Lambda privileges.
- **Notes:** `NoEcho` masks console/API parameter display only; it does not
  protect Lambda environment configuration. Prior reports tracked this as H2
  (session secret) and M2 (Google secret).

### Medium

#### F3. No edge protection: no access logging, throttling, or WAF on the public API

- **Area:** 6. **Confidence:** High.
- **Evidence:** `infra/template.yaml:205-208` — `AWS::Serverless::HttpApi`
  declares only `StageName`. No `AccessLogSettings`, no
  `DefaultRouteSettings` throttling, no `AWS::WAFv2` resource anywhere in the
  template; `scripts/validate_template.py` checks none of these.
- **Exploit path:** compounds F1 — requests are neither rate-limited nor
  attributable, so abuse can run at full speed with no request-level trail.
  Prior report: M1.

#### F4. Any `X-Glide-Session` value forces a full DynamoDB table scan

- **Area:** 5 (resource exhaustion) / 2. **Confidence:** High (independently
  verified by coordinator and by subagent with a fake-client reproduction).
- **Evidence:** `backend/glide/api/deps.py:69-76` accepts any
  `X-Glide-Session` header; `backend/glide/api/demo_store.py:154-161` and
  `:201-207` fall through to `get_sample_snapshot_by_session`;
  `backend/glide/adapters/dynamodb.py:375-382` iterates `self._scan()`;
  `_scan()` at `:121-132` pages through the entire table with no
  `FilterExpression` or projection.
- **Exploit path:** unauthenticated request with a guessed/random session id →
  full table scan → 404. Cost and latency scale with total tenant data; the
  30 s API timeout bounds a request, not the request rate. Verified
  reproduction: 1 Scan, 0 Query for a single request.

#### F5. Development CORS origins are always allowed, with credentials

- **Area:** 2. **Confidence:** High.
- **Evidence:** `backend/glide/api/app.py:181-192` always includes
  `http://localhost:5173` and `http://localhost:4173` alongside the configured
  origin with `allow_credentials=True`.
- **Exploit path:** a hostile page served from a local dev origin (or any
  process able to bind those ports on the victim's machine) can make
  credentialed cross-origin calls and read responses. Low practical reach, but
  it widens the production trust boundary for no functional need. Prior
  report: M3.

#### F6. CI supply chain: tag-pinned actions and no explicit least-privilege permissions

- **Area:** 4 / 6. **Confidence:** High.
- **Evidence:** `.github/workflows/ci.yml:11-12`, `:26-28`, `:39-43` —
  `actions/checkout@v4`, `astral-sh/setup-uv@v5`, `actions/setup-node@v4`;
  the workflow has no `permissions:` block and no dependency-advisory step.
  `uv sync --frozen` / `npm ci` are used (good), but mutable tags mean a
  compromised action tag executes with the default token scope.
- **Exploit path:** upstream action tag hijack or compromised release →
  repository code execution in CI. Prior report: M5; the missing dependency
  scan is L2.

#### F7. MCP tooling forwards broad credentials into unpinned third-party components

- **Area:** 1 / 4. **Confidence:** High on configuration; container/runtime
  provenance unverified offline.
- **Evidence:** `.codex/config.toml:40-80` and
  `tools/mcp/aws/mcp-section.toml:8-35` forward full AWS credential sets
  (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) with
  `docker run -e` into `ghcr.io/awslabs/mcp/*` images referenced with **no tag**
  (implicitly `latest`; `tools/mcp/aws/install-aws-mcps.ps1:5-19` pulls the same
  untagged references). `tools/mcp/github/SETUP.md:17-18` suggests a PAT with
  `contents: read/write` and `issues: read/write`.
  `tools/mcp/playwright/package-lock.json:9-19` resolves `@playwright/mcp` from
  the `"latest"` spec to an **alpha** Playwright build
  (`1.63.0-alpha-2026-08-31`). The **active** project config
  `.codex/config.toml:9-15` sets the Playwright server to
  `default_tools_approval_mode = "auto"` (individual high-risk tools are
  `approve` at `:17-24`), while the staged copy
  `tools/mcp/playwright/mcp-section.toml:11` says `"writes"` — the installed
  copy and the staged source disagree, and the installed copy is the one that
  runs. `scripts/install-mcps.ps1:13` installs with `npm install --no-fund`
  (no version pinning; this line does not disable audit — a sibling report's
  `--no-audit` claim was wrong and is corrected here).
  Verified directly: `tools/mcp/github/package.json` depends on
  `@modelcontextprotocol/server-github: "latest"` (an archived upstream
  package per the sibling report) and
  `tools/mcp/google-calendar/package.json` depends on
  `@zetalytics/mcp-google-calendar: "latest"`, whose setup guide stores a
  service-account JSON key at `tools/mcp/google-calendar/credentials.json`
  (`tools/mcp/google-calendar/SETUP.md:18-25`; the file is covered by the
  `credentials*.json` ignore rule).
- **Exploit path:** a compromised image/package (or unpinned update) harvests
  long-lived AWS keys, a write-scoped GitHub PAT, or the calendar
  service-account key. Local-development scope only, but the credentials are
  broad. Prior report: M4, extended by N6/N7.

#### F8. Google client secret is passed on the `sam deploy` command line

- **Area:** 1 / 6. **Confidence:** High.
- **Evidence:** `scripts/deploy.ps1:23` (mandatory `-GoogleClientSecret`) and
  `:57-62` (`--parameter-overrides "GoogleClientSecret=$GoogleClientSecret"`).
- **Exploit path:** the secret lands in shell history, the SAM/AWS process
  command line, and any wrapper logging, in addition to the stored stack
  parameter. Prior report: N5.

#### F9. No alarms or DLQ monitoring for failure/cost signals

- **Area:** 7 / 6. **Confidence:** High on absence.
- **Evidence:** no `AWS::CloudWatch::Alarm` resources in
  `infra/template.yaml`; the DLQ (`:159-175`) accepts messages after
  `maxReceiveCount: 3` and is never monitored; API/worker errors and throttles
  have no alarm. The USD 75 ceiling is documented but not enforced by any
  template resource.
- **Impact:** silent failure accumulation and no early warning on the F1 abuse
  pattern.

#### F10. Session cookie is stateless for 7 days with no server-side revocation

- **Area:** 2. **Confidence:** High on mechanism; requires cookie theft for
  impact.
- **Evidence:** `backend/glide/api/auth.py:388-394` issues a 7-day session;
  `:262-288` stores only the encrypted session client-side; `:467-471` logout
  deletes the cookie and revokes the Google grant (`app.py:143-150`) but there
  is no server-side session denylist, so a previously captured cookie remains
  valid until its `expires_at` (`auth.py:241-243`). Prior report's "sessions"
  claim covers cookie flags but not revocation.

### Low

#### F11. Model-authored facts and route ids are persisted verbatim

- **Area:** 3. **Confidence:** Medium-High (code read by subagent; coordinator
  did not re-read every branch).
- **Evidence:** `backend/glide/agent/host.py:437-450` stores the model's
  `facts` dict; materialization merges it as the base with deterministic facts
  overriding (`:686-697`); for some reasons the deterministic facts are empty
  (`:737`), so model key/value strings reach `Decision.calculated_facts` and
  are saved (`backend/glide/domain/live.py:268-278`). `route_estimate_id` is
  free text for DECISION reasons other than `insufficient_time` (`:691-697`).
- **Impact:** untrusted text persists in state; it is never written to the
  calendar.

#### F12. Model-supplied strings are echoed into CloudWatch via rejection messages

- **Area:** 3 / 7. **Confidence:** Medium-High.
- **Evidence:** rejection messages embed model-supplied journey keys, actions,
  and estimate ids (`backend/glide/agent/host.py:470-472`, `:519`, `:535`,
  `:544`, `:548`, `:553`, `:607`, `:611`); `AgentProposalMissing` carries that
  text (`backend/glide/agent/strands_runner.py:384-386`) and is an unhandled
  worker exception (`backend/glide/deploy/worker.py:75-77`), so the runtime
  logs it. Log-forging only; no privilege change.

#### F13. Full SQS record is logged on an unparseable body

- **Area:** 7. **Confidence:** High.
- **Evidence:** `backend/glide/deploy/worker.py:94` logs the entire `record`,
  including message body (`user_id`, `run_id`) and the receipt handle (a
  capability for deleting that message). Recommendation recorded in the
  original pass: log only `messageId` and the error type.

#### F14. Place IDs are logged together with `run_id` for 30 days

- **Area:** 7. **Confidence:** High.
- **Evidence:** `backend/glide/agent/host.py:335-340`; place IDs resolve to
  real addresses via GetPlace. Pseudonymous (no user id in the same record) but
  location-revealing. Related: `tools/mcp/playwright` retains traces on failure
  (`frontend/playwright.config.ts:10`) into the gitignored `test-results/`,
  which can capture cookies/headers.

#### F15. Frontend retries non-idempotent POSTs once

- **Area:** 5. **Confidence:** High (coordinator read `frontend/src/api.ts:31-40`).
- **Evidence:** `frontend/src/api.ts:22-40` retries every verb once on a
  transport error; `backend/glide/api/routes/demo.py:150-165` creates a new run
  row and queue message per POST.
- **Impact:** a response lost after processing queues a duplicate run —
  duplicate Bedrock/Location spend and duplicate receipts.

#### F16. Session/config text is interpolated into the agent prompt

- **Area:** 3 / 5. **Confidence:** Medium-High (subagent evidence; exploitability
  is self-scoped).
- **Evidence:** `backend/glide/api/schemas.py:84` (unbounded `time_zone`) →
  `backend/glide/api/routes/demo.py:366-367` →
  `backend/glide/agent/prompts.py:53`; `start_address` at `prompts.py:56` comes
  from `settings.start_place.label` (`backend/glide/agent/strands_runner.py:368-370`),
  also unbounded. Every proposal is re-validated in `ToolHost.accept_proposal`,
  and `time_zone` is never used as a `ZoneInfo` key, so the impact is
  self-scoped prompt manipulation, not a write primitive.

#### F17. Model text is replayed into the repair prompt

- **Area:** 3. **Confidence:** Medium-High.
- **Evidence:** `backend/glide/agent/strands_runner.py:379` passes
  `host.last_rejection` into `build_repair_prompt`
  (`backend/glide/agent/prompts.py:64-70`); rejections embed model-supplied
  values (`host.py:519`, `:544`, `:607`). No privilege change; recorded for
  completeness.

#### F18. State table has no PITR or customer-managed key; S3/SQS use AWS-owned keys

- **Area:** 6. **Confidence:** High.
- **Evidence:** `infra/template.yaml:124-155` — no
  `PointInTimeRecoverySpecification`, no `SSESpecification`; `:42-45` uses
  `AES256` (AWS-owned); `:164` and `:172` use `alias/aws/sqs`. Prior report: L1.

#### F19. Stack outputs expose resource identifiers

- **Area:** 6. **Confidence:** High.
- **Evidence:** `infra/template.yaml:335-347` outputs bucket name, distribution
  id/domain, API URL, table name, queue URL. Only readable by principals with
  stack-describe access; informational.

#### F20. `.codex/config.toml` embeds absolute owner paths; ignore status changed mid-audit

- **Area:** 1 / 6. **Confidence:** High on content; provenance of the ignore
  change is unverified.
- **Evidence:** `.codex/config.toml` contains absolute
  `C:/Users/<owner>/...` paths and credential-forwarding MCP definitions. At
  the start of this audit `.codex/` and `temp/` were **not** ignored; at report
  time `.gitignore` contains `temp/` and `.codex/` (working-tree modification
  observed during the window — see Integrity note). Publication risk is
  therefore mitigated in the current tree, but the file should still not be
  published with machine paths.

## Unverified items

These are explicitly flagged UNVERIFIED; nothing above should be read as proof
of them:

- **Live AWS state** (reserved concurrency, schedule enabled, actual WAF/
  throttling, CloudWatch log contents and any console-side alarms) — no
  network in this environment (proxy refused at `127.0.0.1:9`).
- **Python dependency CVEs** — no network; this `uv` has no `audit`
  subcommand. Versions worth an OSV check include the transitive
  `httpx2==2.12.0` / `httpx2-jsfetch==1.0` entries (`uv.lock:764-787`) pulled in
  via `mcp` → `strands-agents`.
- **`npm audit` clean result** — reported as 0 vulnerabilities for
  `frontend/` and two MCP packages by a subagent; the coordinator could not
  reproduce it offline.
- **Whether F1 is currently exploitable in the deployed account** — the code
  path is verified from source; the stack was reportedly paused at the time of
  the audit (`reserved concurrency 0`), which could not be confirmed.
- **Who can read Lambda configuration in the deployed account** — F2 impact
  depends on IAM permissions that require AWS access to enumerate.
- **MCP browser profile persistence** for the auto-approved Playwright server
  (F7/related finding) — version-dependent.
- **DynamoDB cost magnitude for F4** — needs live table size/consumed-capacity
  metrics.
- **Real Bedrock behaviour against a hostile calendar** — structural
  validation was verified; adversarial model behaviour was not executed.

## Checked and clean

Explicit surfaces reviewed with no issue found (area → evidence of the check):

1. **Secrets:** no AWS keys, private keys, PATs, JWTs, or Google tokens found
   by pattern scan of the working tree; all 11 commits enumerated and scanned
   (`git log --all --diff-filter=A --name-only` shows only expected files).
   `.env`, `secrets/`, `private.md`, `glide-local.db`, `.coverage`, and
   `backend/requirements.txt` are untracked and match `.gitignore`. `.env.example`
   contains placeholders only. No `VITE_*` or embedded frontend credentials.
   Test doubles use literal `fake-*` values (`backend/glide/api/auth.py:477-513`).
2. **AuthN/AuthZ:** OAuth uses per-flow state + PKCE verifier with
   `secrets.compare_digest` (`auth.py:335-355`), pinned redirect URI
   (`auth.py:175-183`, `:186-188`), scope enforcement (`:40-51`, `:195`),
   audience-checked ID token (`:196-208`), HttpOnly/SameSite=Lax/Secure cookies
   (`:267-279`, `:290-299`), expiry enforced on decrypt (`:241-243`, `:254-255`),
   and grant revocation on disconnect (`app.py:143-150`,
   `deploy/credentials.py:125-144`). Ownership/IDOR checks exist for runs
   (`routes/demo.py:175-180`), decisions (`:274-286`), travel blocks
   (`:137-139`), and settings always write the principal's own user id. Sample
   tenants are per-UUID `sample-*` identities; no cross-tenant read path found.
   State-changing routes are POST/PATCH only, and SameSite=Lax blocks
   cross-site cookie sends; no CSRF token is present but no bypass was found.
3. **Injection:** no `subprocess`/`os.system`/`shell=True`/`eval`/`exec`/
   `pickle`/`marshal` in `backend/`, `scripts/`, `tools/`, or workflows (only
   `yaml.load` with a SafeLoader subclass in `scripts/validate_template.py:55`).
   SQLite queries use `?` placeholders and DynamoDB expressions use
   `ExpressionAttributeValues` with tenant-scoped keys; PowerShell loaders
   validate `NAME=value` input (`load_env.ps1:23-25`) and reject newline
   injection (`import_google_oauth.ps1:35-37`). No user-controlled filesystem
   path or template renderer found. Frontend renders all server/model strings
   as JSX text; no `dangerouslySetInnerHTML`/`innerHTML`/`eval`.
4. **Dependencies:** `uv.lock` resolves only `pypi.org` plus the local editable
   project; lockfiles are committed (`uv.lock`, three `package-lock.json` v3
   files); no `http://` or non-registry `resolved` URLs found; no git/tarball
   dependencies found. (CVE status remains UNVERIFIED per above.)
5. **SSRF/deserialization/parsing:** the only `urlopen` targets the fixed
   Google revocation endpoint with a fixed URL (`deploy/credentials.py:26-43`);
   Google auth uses the library's fixed endpoints
   (`api/auth.py:31-32`, `:160-201`); Amazon Location and Google Calendar go
   through boto3/google client libraries with no user-supplied endpoint or URL.
   No `endpoint_url` overrides found. No upload/multipart/archive/XML/PDF
   parsing exists in the API; no `pickle`/`eval`/unsafe `yaml.load` sinks.
6. **CI/IaC hardening that is present:** S3 public access fully blocked with
   SSE and an OAC pinned by source ARN (`template.yaml:42-76`); CloudFront
   HTTPS-only with TLS 1.2 and cache disabled for `/api/*` (`:86-102`,
   `:108-113`); SQS FIFO with KMS, DLQ, and `maxReceiveCount: 3` (`:159-175`);
   DynamoDB TTL enabled and tenant-scoped key/GSI design (`:124-155`); API IAM
   scoped to the specific table, queue, session secret ARN, and
   `geo-places:SearchText` (`:231-251`); worker IAM scoped to
   `glide/tokens/*`, the Bedrock model/profile, and three geo actions
   (`:282-304`); 30-day log retention per function (`:187-203`, `:216`,
   `:269`, `:318`); no secret literals or committed `samconfig.toml`.
7. **Logging/errors:** only eight `logger.*` call sites exist (in
   `agent/host.py`, `agent/strands_runner.py`, `deploy/worker.py`); none logs
   tokens, emails, event text, prompts, or exception contents. Persisted
   failures store only the exception class name
   (`backend/glide/api/run_service.py:49-59`). All client-facing
   `HTTPException.detail` values are curated constants; there is no
   `exc_info=True`, `str(exc)` in a response, `traceback` import, or
   `debug=`/custom 500 handler, so unhandled errors return FastAPI's generic
   body. Frontend has no console logging and keeps only the sample session id
   in `localStorage` (`frontend/src/api.ts:52-65`); the live identity is in the
   HttpOnly cookie.

## Deduplication map to the previous report

| Previous ID | Consolidated finding |
| ----------- | -------------------- |
| H1 | F1 |
| H2 | F2 (session key); F2 also absorbs M2 |
| M1 | F3 |
| M2 | F2 |
| M3 | F5 |
| M4 | F7 |
| M5 | F6 |
| L1 | F18 |
| L2 | F6 (scanning) + Unverified (CVEs) |
| Continuation N1 | F4 |
| Continuation N2 | F16 |
| Continuation N3 | F17 |
| Continuation N4 | F15 |
| Continuation N5 | F8 |
| Continuation N6/N7 | F7 |
| Coordinator additions | F9, F10, F11–F14, F19, F20 |

## Coverage and method notes

- Seven area agents were spawned as requested. Runtime faults made delivery
  unreliable: several agent threads received empty task payloads and asked for
  direction (areas 2, 4 and 6 on first attempt), and every subagent spawned by
  an area agent failed with an unsupported model name (`gpt-5.6-terra`).
  Replacement threads were spawned; areas 1, 3, 5 and 7 ultimately returned
  evidence-bearing reports. Areas 2 and 6 were completed by coordinator
  verification, and area 4 was completed from coordinator checks plus the
  sibling reports' lockfile/MCP evidence. This is why some findings above cite
  "coordinator" as the source. No area was left unscanned.
- **Integrity note (read-only constraint):** one area agent, whose task payload
  was lost, picked up `prompts/continue-security-audit.md` instead and
  appended a `## Continuation — 2026-09-12` section to the untracked
  `docs/security-audit-2026-09-11.md` before being interrupted; the original
  content is preserved and its findings were folded into this report. In
  addition, during the audit window the working tree changed in ways unrelated
  to this audit: `.gitignore` gained `temp/` and `.codex/`, and
  `scripts/verify_deployed_sample.py` changed its run timeout from 120 s to
  300 s, and a `.playwright-mcp/` directory appeared. Those changes were not
  made by this report's author and were left untouched; they are flagged here
  because they affect F20 and any future reproduction of the exact snapshot.
- No fixes were applied. This document is the only file created by this audit.
- **Corrections to subagent claims made during consolidation:** (a) the
  Playwright MCP approval mode is `"auto"` in the *installed*
  `.codex/config.toml:15` but `"writes"` in the staged
  `tools/mcp/playwright/mcp-section.toml:11` — the two files disagree, so the
  finding is scoped to the installed config; (b) the claim that
  `scripts/install-mcps.ps1` uses `npm install --no-audit` was checked and
  refuted — line 13 uses `--no-fund`; (c) the claim that all `tools/mcp/*`
  npm packages have no lockfile is partly wrong: `github/package-lock.json`
  and `playwright/package-lock.json` exist, but their root specs still say
  `"latest"`.
- The single highest-value next step, if one is wanted, is F1: gate the demo
  router on `GLIDE_ENV` (or require auth in production), since it converts the
  anonymous exposure into a bounded, authenticated feature.
