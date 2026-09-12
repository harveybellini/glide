# Prompt: Continue the security audit

You are continuing a security audit of this repository ("Glide": Python/FastAPI
backend on AWS Lambda + API Gateway, DynamoDB/SQLite state, SQS jobs, Google
OAuth, a React/Vite frontend, and local MCP tooling).

First read docs/security-audit-2026-09-11.md completely. That report is the
baseline: its findings and "verified as solid" claims are not to be re-litigated
unless you find evidence they are wrong. Your job is to close the gaps listed
in its "Coverage honesty" section and to validate the fixes if any have since
been applied (check git log/diff against the report's remediation items).

HARD RULES
- Read-only audit. Do not modify any files, config, or git state.
- Never print actual secret values or token contents; reference paths only.
- Report concrete evidence as file:line references.
- Do not inflate severity; mark uncertain items as "needs confirmation."

SCOPE — complete line-by-line reviews of the surfaces the first pass only
grepped. Read each file fully:

1. backend/glide/agent/host.py and strands_runner.py
   - Prove or refute: every accepted agent proposal is validated before any
     calendar/state write (identity of journey keys, occurrence IDs, times,
     route estimates, action enum). Look for any path where model output can
     cause writes outside validated bounds, cross-user writes, or unbounded
     work. Confirm the mutation guard and settings-revision recheck run before
     commit in live/processor.py.
2. backend/glide/adapters/dynamodb.py and sqlite.py
   - Confirm all queries are parameterized/built with ExpressionAttributeValues;
     no string interpolation into SQL/expression syntax; check TTL usage,
     key design, and any scan/filter that could leak across tenants.
3. backend/glide/adapters/google_calendar.py
   - Token refresh/revocation safety, error handling that could expose tokens
     in exceptions or logs, calendar-ID handling, and whether event titles/
     locations are passed anywhere they could be interpreted as instructions.
4. backend/glide/jobs/* (queue.py, sqs_queue.py, dispatcher.py, worker.py)
   - Message visibility/timeout handling, DLQ policy, poisoning/failed-message
     loops, and whether job payloads could carry user-controlled content into
     privileged contexts.
5. frontend/src/* (api.ts, App.tsx, all components) plus vite.config.ts and
   playwright.config.ts
   - XSS via rendering user/calendar/LLM-derived strings, URL/redirect handling,
     token or session exposure in code, storage (localStorage), and any
     unsafe dev-server/proxy configuration.
6. tools/mcp/* (all launch.mjs, *.ps1, *.toml, package.json, SETUP.md)
   - What credentials are forwarded where, whether any command is built from
     unvalidated input, image pinning, and least-privilege of the AWS role and
     GitHub PAT scopes suggested.
7. scripts/*.ps1 (deploy, build_lambda, import_google_oauth, load_env,
   live_smoke)
   - Secrets echoed to console, insecure parameter passing, hardcoded account
     IDs/regions/ARNs, and unsafe PowerShell patterns.
8. Dependency audit: backend/glide/pyproject.toml (root pyproject.toml) +
   uv.lock, frontend/package.json + package-lock.json
   - Identify any package with known CVEs you can determine from version data
     alone, unpinned/insecure ranges, and any lockfile entries that are unusual.
     If network is available, run `uv audit` and `npm audit`; otherwise say so.
9. Re-verify the two High findings from the report with current code:
   - H1: is POST /api/demo/session (and the demo principal path) still
     reachable with GLIDE_ENV=production?
   - H2: is GLIDE_SESSION_SECRET still resolved into Lambda env at deploy time?
   Also check whether M1–M5 and L1–L2 from the report have been addressed.

OUTPUT
Append a new section "## Continuation — 2026-09-12" to
docs/security-audit-2026-09-11.md (keep the existing content intact), or if you
cannot edit files, return the full markdown section in your final answer.
Structure it as:
- New findings, each with severity (Critical/High/Medium/Low), file:line
  evidence, impact, and a one-line fix.
- Corrections to the original report, if any, with evidence.
- Confirmation or refutation of each item 1–9, one line each.
- Status of the original H1/H2/M1–M5/L1–L2 items (fixed / open / not yet
  started), verified against the current tree.
- A short "still not verified" list so the next pass knows what remains.
