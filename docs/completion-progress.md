# Glide submission progress

Last verified: 10 September 2026. This file records only checks whose results
were observed in the current workspace or account state. It contains no
private owner data or credentials.

## Verified checkpoints

| Checkpoint | Evidence | Date |
| --- | --- | --- |
| Offline implementation fixes (N1â€“N5) | `pytest`: 266 passed; `ruff check .`: clean | 10 Sep |
| Frontend production checks | `tsc -b && vite build` passes (34 modules); typecheck is part of that pipeline | 10 Sep |
| AWS login | Local `glide` profile authenticates (root login session, `eu-west-1`) | 10 Sep |
| Bedrock access | One `Converse` call to `eu.amazon.nova-2-lite-v1:0` returned a valid reply (57 tokens total); account verification completed | 10 Sep |
| Amazon Location Places | Two independent `SearchText` calls resolved Big Ben and The Shard (with `BiasPosition`, the required geographic selector) | 10 Sep |
| Amazon Location Routes | `CalculateRoutes` returned a real driving duration (513 s, quality `live`) between the two venues | 10 Sep |
| Live agent loop | `scripts/live_smoke.py` completed a real Strands/Bedrock run and proposed `create feasible destination` | 10 Sep |
| Spending | Cost Explorer reports USD 0.00 unblended cost for September so far; USD 75 remains the planned ceiling | 10 Sep |
| SAM validation | `sam validate --lint` reports the template valid after fixes (policy-template name, Lambda `LogGroup` ARN, circular dependency removed via the `FrontendOrigin` parameter) | 10 Sep |
| Lambda packaging | `scripts/build_lambda.ps1` builds a 51 MB Linux/x86_64 bundle for Python 3.12 via uv (Windows-only `pywin32` excluded); handlers verified inside the zip | 10 Sep |
| Canonical sample | `scripts/run_sample.py`: create â†’ conflict (10 min shortfall) â†’ move â†’ update â†’ idempotent repeat â†’ delete â†’ direct journey | 10 Sep |
| Ten-run regression | `scripts/run_ten_runs.py`: 10/10 canonical runs, mean 0.1 ms, fixture providers | 10 Sep |
| Contract docs | `docs/openapi.json` regenerated from the current routes (60 lines added) | 10 Sep |
| Public fictional sample (API) | Fresh session â†’ first check yields one block plus one shortfall decision â†’ move the middle appointment â†’ recheck yields two blocks and zero decisions â†’ repeat is idempotent â†’ reset clean | 10 Sep |
| Publication hygiene | Secret scan over tracked files found only test fixtures; no credentials in source | 10 Sep |
| Architecture | Diagram regenerated: Google Calendar box now reads appointments and writes owned blocks in the primary calendar | 10 Sep |

## In progress

- **AWS deployment.** The deploy script was corrected (two-pass `FrontendOrigin`
  resolution, uv-built zip CodeUri, CLI path resolution) and started against
  the live account, but the run was interrupted before the stack completed.
  The bundle and frontend artifacts are rebuilt and ready to retry. Stack
  state must be re-inspected before the next run; `sam deploy` is idempotent.
  Retry still requires restored AWS network/approval access.

## Remaining

1. Finish the CloudFormation deployment and confirm `/api/health`, sample
   creation, the deployed OAuth callback, and background processing.
2. Register the deployed OAuth redirect URI in Google Cloud, connect the test
   account, and record ten real maintenance sequences (including one
   scheduled run with the browser closed).
3. Verify deployed idempotency, manual-edit/deletion respect, refresh/reconnect,
   pause, and disconnect against real Google data.
4. Re-authenticate GitHub CLI (the stored token is invalid), create the public
   `glide` repository, push, and confirm CI passes.
5. Update submission assets against the shipped release (story, architecture
   export, screenshots, video materials, fields), then the owner uploads the
   video, enters the Builder identifier, confirms eligibility, and submits.
6. Keep the hosted judge experience available through 9 October 2026 and
   monitor gross usage against the USD 75 ceiling.

## Owner action items

- Allow the deployment to complete (restore escalation for the AWS commands,
  or run `scripts/deploy.ps1` yourself and share the stack outputs).
- Register the deployed `https://<distribution>/api/auth/google/callback`
  redirect URI in the Google Cloud OAuth client.
- Refresh GitHub CLI auth (`gh auth login`) for `harveybellini`.
- Later: connect the Google test account in the browser, upload the video,
  enter the AWS Builder identifier, confirm eligibility, and press Submit.
