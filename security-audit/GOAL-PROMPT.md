# /goal prompt — security remediation

Copy the block below into a new agent session as the `/goal` objective. It is
self-contained: it names both audit documents, the execution order, the
constraints, and the definition of done. Nothing in the block asks the agent to
deploy or push.

---

```text
Remediate the security findings in this repository (Glide: Python/FastAPI
backend deployed on AWS Lambda + API Gateway, DynamoDB/SQLite state store, SQS
FIFO job pipeline, React/Vite frontend). Working directory:
C:\Users\harve\Documents\Coding\Coding\Agents for Humans Hackathon

Read both of these before changing anything:
- security-audit/REPORT.md — the full read-only audit (findings F1–F20 with
  severity, exact file:line, exploit path, confidence, and an explicit
  unverified list).
- security-audit/NEXT-STEPS.md — the fix order (S1–S11) with the exact files,
  required changes, acceptance tests, decisions, and a "do not do" list.

Implement in this order, one commit per item, message naming the item:
S1 stop the production demo endpoints from creating unbounded billed work
   (High: gate/throttle demo routes or take sample runs off Bedrock);
S2 fetch the session key and Google client secret from Secrets Manager at
   runtime instead of resolving them into Lambda environment variables (High);
S3 remove the full DynamoDB table scan triggered by any X-Glide-Session header;
S4 add API Gateway access logging and throttling;
S5 restrict CORS to the production origin only;
S6 pin GitHub Actions by commit SHA and add a least-privilege permissions block;
S8 stop passing the Google client secret on the sam deploy command line;
S9 add alarms for DLQ depth, worker errors, and API 5xx/throttles;
S7 pin MCP npm specs and container image digests, and reduce MCP credential
   scope;
S10 session revocation — if the product decision is unclear, record it as
   blocked and continue rather than guessing;
S11 the quick hardening items: minimize the SQS parse-failure log, make the
   frontend retry idempotent verbs only (or honour an idempotency key), bound
   the prompt-interpolated settings, enable DynamoDB point-in-time recovery,
   and keep machine paths out of published MCP config.

Rules:
- Re-verify every file:line reference against current HEAD before editing. The
  audit snapshot is 6c0606e; lines may have moved.
- Do not revert, reformat, or commit unrelated working-tree changes: a docs
  batch, .gitignore, scripts/verify_deployed_sample.py, and .playwright-mcp/
  belong to another workstream.
- Never print, commit, or log secret values, tokens, or .env/secrets contents.
- Do not deploy to AWS, do not push, and do not make live AWS calls without
  explicit human approval. If an item needs a live call or an owner decision,
  mark it blocked in the final report and continue with the rest.
- Preserve existing hardening: SQS DLQ + KMS, least-privilege IAM statements,
  and the agent proposal validation in backend/glide/agent/host.py and
  backend/glide/domain/live.py. Do not weaken existing tests.
- Do not fix findings outside S1–S11 without asking.

Verification after each item and at the end:
  uv run pytest -q
  uv run ruff check .
  uv run python scripts/validate_template.py
  cd frontend; npm ci; npm run typecheck; npm run build
If the environment has no network, run what works and record the rest as
UNVERIFIED rather than skipping it silently. Set UV_CACHE_DIR to a writable
path inside the workspace if uv complains.

Deliverable: the code/template/script changes plus tests that prove each
acceptance criterion in NEXT-STEPS.md, and a final table mapping every S-item to
status (done / blocked / unverified), files changed, tests run, and remaining
risk. Flag anything you could not verify explicitly.
```
