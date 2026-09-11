# Glide submission progress

Last verified: 11 September 2026, evening session. This file records only
checks whose results were observed in the current workspace or account state.
It contains no private owner data or credentials.

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
| Offline suite (current tree) | `pytest`: 268 passed; `ruff check .`: clean; frontend `tsc -b`: clean. The local `npm run build` and Playwright runs cannot start in this sandbox (esbuild/child-process spawn is denied); CI runs those gates on the push | 11 Sep |
| Deployed sample run (real Bedrock) | Run `run-1f7fa276a54840719ecee6a6a15a344f` reached a terminal `failed` state with `safe_failure_code=AgentDeadlineExceeded` about 208 s after it was queued, against the worker's 200 s agent deadline. The worker did pick the job up; the site, queue, and worker wiring are live | 11 Sep |
| Deployed verifier | `scripts/verify_deployed_sample.py` run-poll window raised from 120 s to 300 s so a deadline-length deployed run is observed rather than reported as a timeout | 11 Sep |
| Deployed API availability | Between ~19:03 and ~19:11 BST every route through CloudFront returned `503 {"message":"Service Unavailable"}` (`X-Cache: Error from cloudfront`), including `/api/health`; `/api/health` answered 200 again from 19:12, then a fresh sample session and run poll returned 503 again at ~19:15. The origin is flapping, so deployed verification cannot be trusted while it is in this state | 11 Sep |
| Deployed verification attempt | The retry created session `7c0ccacc02f241c4bc29b16e503260c7` and queued run `run-6612c7850dec4aca901d8848a0907efc`, then failed on a 503 while polling. No block/receipt evidence from this attempt | 11 Sep |
| AWS CLI session | The cached root sign-in session for the `glide` profile passed its access-token expiry mid-session; refresh attempts now fail (`invalid_grant`) and the CLI cannot write its login cache from this sandbox. AWS calls need an owner `aws login` | 11 Sep |
| Google redirect URI | Fetching the deployed authorize URL returns `Error 400: redirect_uri_mismatch`, so the deployed callback is not yet registered in the Google Cloud OAuth client | 11 Sep |
| GitHub CLI | `gh auth status` now reports a valid session for `harveybellini` (`repo`, `workflow`), so the publication blocker is cleared | 11 Sep |
| Public repository | `https://github.com/harveybellini/glide` created public with the 12-commit development history mirrored through the GitHub API; repo loads signed out (HTML 200, raw README 200, API `private: false`); CI workflow active on push `714bd1f9` | 11 Sep |
| CI run on `714bd1f9` | `backend` green (`uv sync --frozen`, 268 pytest, ruff, template validation); `frontend` green (typecheck + Vite build); `e2e` green on every test step (including the 4 Playwright checks). The job only failed in `astral-sh/setup-uv`'s post-job cache prune, which was re-run green | 11 Sep |
| CI hardening | `.github/workflows/ci.yml` sets `prune-cache: false` on both `setup-uv` steps; commit `66d1a270` on `main` completed CI green | 11 Sep |
| Deployed sample run, second attempt | Run `run-6612c7850dec4aca901d8848a0907efc` also ended `failed` with `safe_failure_code=AgentDeadlineExceeded` (ended 18:17 UTC, ~200 s after it was queued). Two consecutive deployed runs hit the 200 s agent deadline, so the worker loop is consistently out of time rather than occasionally | 11 Sep |

Deployment fixes found and applied against the real account while validating:
SAM policy-template name, CloudFront/API circular dependency, CloudFront
disabled-caching policy rules (headers/cookies), Lambda `LoggingConfig`
log-group name format, account concurrency minimum, missing
`bedrock:InvokeModelWithResponseStream` permission, Strands' foundation-model
ARN resolution, and Mangum's API Gateway stage base path.

## In progress

- **Deployed worker reliability.** The Bedrock IAM widening (`e4bcace`) and the
  wider turn budget (`6c0606e`) are committed and the stack was redeployed
  (`UPDATE_COMPLETE` at 17:57 UTC). Two deployed sample runs still ended in
  `AgentDeadlineExceeded` at the 200 s agent deadline, so the loop needs either
  a longer deadline or fewer model turns (a cached/pre-computed route or a
  coarser tool surface). CloudWatch worker logs are the next diagnostic step
  and need a refreshed AWS session.
- **Deployed origin flapping.** The API origin alternates between healthy and
  `503 Service Unavailable`, which also prevents the verifier from observing a
  complete run. Whether this is a reserved-concurrency pause, throttling, or a
  concurrent change to the stack can only be told from the account.
- **Sandbox limits on git and frontend tooling.** This session cannot write
  inside `.git` (commits/pushes from the workspace fail with `Permission
  denied`) and cannot spawn esbuild, so the publication was mirrored through
  the GitHub API and the frontend gates are delegated to CI. The workspace
  still holds the same 17 modified files as uncommitted changes; the public
  tip carries their content.
- **Concurrent workspace session.** Another session is actively editing the
  frontend, the screenshots, and separate security-audit files in this same
  checkout. The public repository therefore reflects the tree as of ~19:07 BST
  and needs a re-publish once that UI work lands.

## Remaining

1. Stabilize the deployed agent loop: refresh the AWS session, read the worker
   logs for the failed run, decide between a longer deadline and fewer model
   turns, redeploy, then verify deployed reconciliation, idempotent repeats,
   and one scheduled maintenance run with the browser closed.
2. Register the deployed OAuth redirect URI
   (`https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback`) in the
   Google Cloud OAuth client, connect the test account, and record ten real
   maintenance sequences.
3. Verify deployed refresh/reconnect, manual-edit/deletion respect, pause, and
   disconnect against real Google data.
4. Confirm the public repository's CI run is green and keep the README's live
   demo link accurate as the deployed fix lands.
5. Finalize submission assets against the shipped release (story, architecture
   export, screenshots, video materials, fields), then the owner uploads the
   video, enters the Builder identifier, confirms eligibility, and submits.
6. Keep the hosted judge experience available through 9 October 2026 and
   monitor gross usage against the USD 75 ceiling.

## Owner action items

- Run `aws login` (or `aws login --profile glide`) in a normal PowerShell
  window: the cached root sign-in session expired at ~17:43 UTC and every
  deploy, log, and DynamoDB check is blocked until it is refreshed.
- Register `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback` as
  an authorized redirect URI in the Google Cloud OAuth client.
- Later: connect the Google test account in the browser, upload the video,
  enter the AWS Builder identifier, confirm eligibility, and press Submit.
