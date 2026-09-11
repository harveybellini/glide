# Glide submission progress

Last verified: 11 September 2026. This file records only checks whose results
were observed in the current workspace or account state. It contains no
private owner data or credentials.

## Verified checkpoints

| Checkpoint | Evidence | Date |
| --- | --- | --- |
| Offline implementation fixes (N1-N5) | `pytest`: 266 passed; `ruff check .`: clean | 10 Sep |
| Frontend production checks | `tsc -b && vite build` passes (34 modules); typecheck is part of that pipeline | 10 Sep |
| AWS login | Local `glide` profile authenticates (root login session, `eu-west-1`) | 10 Sep |
| Bedrock access | One `Converse` call to `eu.amazon.nova-2-lite-v1:0` returned a valid reply (57 tokens total); account verification completed | 10 Sep |
| Amazon Location Places | Two independent `SearchText` calls resolved Big Ben and The Shard (with `BiasPosition`, the required geographic selector) | 10 Sep |
| Amazon Location Routes | `CalculateRoutes` returned a real driving duration (513 s, quality `live`) between the two venues | 10 Sep |
| Live agent loop | `scripts/live_smoke.py` completed a real Strands/Bedrock run and proposed `create feasible destination` | 10 Sep |
| Spending | Cost Explorer reports USD 0.00 unblended cost for September so far (usage reporting lags); USD 75 remains the planned ceiling | 10 Sep |
| SAM validation | `sam validate --lint` reports the template valid after the fixes listed below | 10 Sep |
| Lambda packaging | `scripts/build_lambda.ps1` builds a 51 MB Linux/x86_64 bundle for Python 3.12 via uv (Windows-only `pywin32` excluded); handlers verified inside the zip | 10 Sep |
| Canonical sample | `scripts/run_sample.py`: create -> conflict (10 min shortfall) -> move -> update -> idempotent repeat -> delete -> direct journey | 10 Sep |
| Ten-run regression | `scripts/run_ten_runs.py`: 10/10 canonical runs, mean 0.1 ms, fixture providers | 10 Sep |
| Contract docs | `docs/openapi.json` regenerated from the current routes | 10 Sep |
| Public fictional sample (local API) | Fresh session -> first check yields one block plus one shortfall decision -> move the middle appointment -> recheck yields two blocks and zero decisions -> repeat is idempotent -> reset clean | 10 Sep |
| Publication hygiene | Secret scan over tracked files found only test fixtures; no credentials in source | 10 Sep |
| Architecture | Diagram regenerated: Google Calendar box now reads appointments and writes owned blocks in the primary calendar | 10 Sep |
| AWS deployment | Stack `glide` is `CREATE_COMPLETE`/`UPDATE_COMPLETE` in `eu-west-1`; frontend published and cache invalidated | 10 Sep |
| Deployed site | `https://d3tvxy281s2u11.cloudfront.net/` returns 200 and `/api/health` returns `{"status":"ok","mode":"sample","version":"0.1.0"}` | 10 Sep |
| Deployed sample creation | `POST /api/demo/session` returns 201 through CloudFront | 10 Sep |
| Deployed pipeline | One sample check completed through SQS -> worker -> DynamoDB -> real Bedrock: status `needs_input`, one block, one decision | 10 Sep |
| Git checkpoint | Work committed as `98563a3` (60 files, +2382/-392); no remote configured yet | 10 Sep |
| Re-verification | `pytest` 266 passed, `ruff` clean, deployed `/api/health` returns 200 ok; GitHub CLI token still invalid; no git remote configured | 11 Sep |

Deployment fixes found and applied against the real account while validating:
SAM policy-template name, CloudFront/API circular dependency, CloudFront
disabled-caching policy rules (headers/cookies), Lambda `LoggingConfig`
log-group name format, account concurrency minimum, missing
`bedrock:InvokeModelWithResponseStream` permission, Strands' foundation-model
ARN resolution, and Mangum's API Gateway stage base path.

## In progress

- **Deployed worker reliability.** Runs sometimes complete (one block plus one
  decision observed) and sometimes fail with `AgentProposalMissing` after the
  agent's turn budget is reached. This is being diagnosed against worker logs;
  it does not block the public site, which serves and creates sessions.
- **Uncommitted Bedrock IAM change.** The working tree `infra/template.yaml`
  (edited after commit `98563a3`, not yet deployed) widens Bedrock resources
  from the single inference profile to the foundation-model ARN plus
  `inference-profile/*`. Commit this and redeploy the stack before claiming the
  deployed agent-loop fix is confirmed. The same uncommitted batch also
  contains 17 modified doc/infra files and untracked MCP tooling
  (`.codex/`, `tools/`, `scripts/install-mcps.ps1`, `docs/mcp-setup.md`).

## Remaining

1. Stabilize the deployed agent loop: commit and redeploy the uncommitted
   Bedrock IAM change in `infra/template.yaml`, diagnose `AgentProposalMissing`
   against worker logs, then verify deployed reconciliation, idempotent
   repeats, and one scheduled maintenance run with the browser closed.
2. Register the deployed OAuth redirect URI
   (`https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback`) in the
   Google Cloud OAuth client, connect the test account, and record ten real
   maintenance sequences.
3. Verify deployed refresh/reconnect, manual-edit/deletion respect, pause, and
   disconnect against real Google data.
4. Re-authenticate GitHub CLI (the stored token is invalid), create the public
   `glide` repository, push, and confirm CI passes.
5. Finalize submission assets against the shipped release (story, architecture
   export, screenshots, video materials, fields), then the owner uploads the
   video, enters the Builder identifier, confirms eligibility, and submits.
6. Keep the hosted judge experience available through 9 October 2026 and
   monitor gross usage against the USD 75 ceiling.

## Owner action items

- Register `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback` as
  an authorized redirect URI in the Google Cloud OAuth client.
- Refresh GitHub CLI auth (`gh auth login`) for `harveybellini`.
- Later: connect the Google test account in the browser, upload the video,
  enter the AWS Builder identifier, confirm eligibility, and press Submit.
