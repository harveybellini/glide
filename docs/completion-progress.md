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
| Google redirect URI | **Registered.** The deployed authorize URL now returns Google's consent page (HTTP 200) with no `redirect_uri_mismatch`; `redirect_uri` is `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback` and the scope is `openid email https://www.googleapis.com/auth/calendar.events.owned`. The remaining Google step is the owner's browser consent with the test account | 11 Sep |
| GitHub CLI | `gh auth status` now reports a valid session for `harveybellini` (`repo`, `workflow`), so the publication blocker is cleared | 11 Sep |
| Public repository | `https://github.com/harveybellini/glide` created public with the 12-commit development history mirrored through the GitHub API; repo loads signed out (HTML 200, raw README 200, API `private: false`); CI workflow active on push `714bd1f9` | 11 Sep |
| CI run on `714bd1f9` | `backend` green (`uv sync --frozen`, 268 pytest, ruff, template validation); `frontend` green (typecheck + Vite build); `e2e` green on every test step (including the 4 Playwright checks). The job only failed in `astral-sh/setup-uv`'s post-job cache prune, which was re-run green | 11 Sep |
| CI hardening | `.github/workflows/ci.yml` sets `prune-cache: false` on both `setup-uv` steps; commit `66d1a270` on `main` completed CI green | 11 Sep |
| Deployed sample run, second attempt | Run `run-6612c7850dec4aca901d8848a0907efc` also ended `failed` with `safe_failure_code=AgentDeadlineExceeded` (ended 18:17 UTC, ~200 s after it was queued). Two consecutive deployed runs hit the 200 s agent deadline, so the worker loop is consistently out of time rather than occasionally | 11 Sep |
| Deployed verifier retries | `scripts/verify_deployed_sample.py` now retries `429/5xx` responses and request errors, so a brief origin flap is not reported as a deployment failure. The first run with this change still exhausted six attempts against a ~25 s 503 window | 11 Sep |
| Hardening snapshot published | `main` at `e6fcc071` carries the working tree (runtime Secrets Manager lookups, production CORS, API access logging and route throttling, deterministic sample planning, refreshed screenshots, submission fields, verifier retries, three new test modules, Dependabot config). A byte-for-byte comparison of the 150 tracked files against the published tree reports 0 mismatches | 11 Sep |
| Offline suite after hardening | `pytest`: 284 passed; `ruff check .`: clean; `scripts/validate_template.py`: OK | 11 Sep |
| CI on the hardening snapshot | `CI` completed **success** on `e6fcc071` (`backend`, `frontend`, `e2e`). GitHub also opened Dependabot PRs from the new config; two major-version bumps (TypeScript 7, Vite 8) fail on their own branches and are unrelated to `main` | 11 Sep |
| Progress log published | `main` at `20f7a770` adds this record; `CI` completed success | 11 Sep |
| Deployed availability, latest sample | 10-second health probes for 2.5 minutes: 2 ok, 13 failed (`503`). A burst of five `POST /api/demo/session` calls returned five `503`s. No `429` has been observed, so the route throttling in the working tree is not deployed yet | 11 Sep |
| Offline suite, later tree | `pytest`: 300 passed; `ruff check .`: clean; `scripts/validate_template.py`: OK. `sam validate --lint` cannot run in this sandbox (SAM writes `AppData\Roaming\AWS SAM\metadata.json`, which is outside the writable roots) | 11 Sep |
| Deployed outage root cause | The account allows 10 concurrent Lambda executions. The worker ran 10 concurrent sample jobs, each spending 120-220 s in Bedrock before failing (`agent stop_reason=<limit_turns>` then `AgentDeadlineExceeded`), which starved every other function. `AWS/Lambda` shows `ConcurrentExecutions` pinned at 10 and both worker and API `Throttles` climbing (worker 67/5 min at peak); the API's own durations were 3-512 ms, so it was throttled, not broken | 11 Sep |
| Blast radius | Job queue held 81 messages with 14 in flight and the dead-letter queue held 74 failed jobs; `DispatcherFunctionSchedule` was `ENABLED` (modified 18:42) and kept adding runs | 11 Sep |
| Mitigations applied | `DispatcherFunctionSchedule` set to `DISABLED`; worker SQS event source mapping set to `ScalingConfig.MaximumConcurrency=2`; worker `GLIDE_AGENT_MODE` set to `deterministic` so the backlog drains without further Bedrock spend. All three are configuration-only and reversible | 11 Sep |
| Deployed availability after mitigation | 12/12 `/api/health` probes returned 200 over two minutes (previously 2 of 15) | 11 Sep |
| Deployed sample verification | Fresh deployed session: first check `needs_input` in 0.8 s with one block and one open shortfall decision; moving the middle appointment and rechecking gives `completed`, two blocks, zero decisions in 0.8 s; repeating gives two blocks, zero decisions, all receipts `unchanged`, in 0.9 s. Deployed reconciliation and idempotent repeats are verified | 11 Sep |
| AWS sign-in session | The root sign-in session only lasts a short time and every refresh attempt from this sandbox fails (`invalid_grant`) because the CLI cannot persist the rotated token under `~/.aws/login`. The owner's fresh `aws login` restored access for about twenty minutes; the deployment work below needs another one | 11 Sep |
| Dead-letter queue drained | All 76 messages archived to `temp/dlq-archive-2026-09-11.json` (13 anonymous `sample-*` tenants, sent 2026-09-10 22:26Z to 2026-09-11 18:32Z, the outage window) and then deleted in batches. Queue reports 0 visible / 0 in flight, so `glide-dlq-depth` clears on its next evaluation | 11 Sep |
| Deployed OAuth regression (found + fixed) | The stack deployed at 20:18 BST resolved the Google client secret at runtime but `create_app` still required `GOOGLE_CLIENT_SECRET` in the environment, so the API served `provider_available: false` and `/api/auth/google/start` returned 503. `create_app` now accepts an explicit `GoogleOAuthConfig` and the deployed entrypoint passes it; three regression tests added | 11 Sep |
| Live OAuth fixed end to end | Two further bugs surfaced during the owner's consent and are fixed and redeployed: oauthlib raised its scope-change `Warning` as an exception because Google answers the `email` scope with `userinfo.email` (now relaxed while the required-scope guard normalises both spellings), and the token secret name used `glide/tokens/google:<sub>`, which Secrets Manager rejects (now sanitised with a short digest so the mapping stays injective) | 11 Sep |
| Google connection | The owner added the test account as a Google test user and completed consent; tenant `google:1009â€¦0490` exists with a stored refresh grant, the deployed API reports the account connected, and an owner-scoped session was minted server-side to drive the deployed API for verification (no browser cookie handling) | 11 Sep |
| Live run, first attempt | One real `trigger=live` run through the deployed worker (Bedrock + Amazon Location + Google read) ended `failed` after ~69 s with `AgentProposalMissing` (the agent hit `limit_turns` on both the first and repair pass; final rejection code `invalid_journey`). Worker logs name the run and stop reasons, so the loop needs a fix before the live proof can be recorded | 11 Sep |
| Live agent loop, root causes | Reproduced locally against the real tenant with a recording host. Four defects: (1) the proposal schema advertised `update`/`noop`/`skip`, which the validator always rejects; (2) `lookup_place` candidates were not accepted by `estimate_journey`, so anything the model resolved mid-run was unusable and it re-looked-up until the turn cap; (3) the model was required to decide the `start_place -> first event` pair even though the tenant has no start address, producing `insufficient_time`/`remove`/`create` proposals that could only be rejected; (4) the repair pass continued a conversation that ended at the turn cap, which Bedrock refuses ("a conversation must start with a user message") | 11 Sep |
| Live agent loop, fixes | Proposal schema now offers only `create`/`remove`/`decision`; looked-up places join the routable reference set; the no-start-address pair is decided by the host (reason `unknown_start`) and removed from the model's required set; the repair pass runs on a fresh agent with the full task prompt; the Bedrock model forces `toolChoice: any` so a turn cannot be spent on text alone; turn budget 16 -> 24 behind the unchanged 200 s deadline; the system prompt carries an explicit per-pair recipe | 11 Sep |
| Live agent loop, local proof | With the fixes, the real Bedrock/Nova Lite loop completed against the live tenant data and produced three accepted plans: `unknown_start` decision, then `create feasible` for Big Ben -> The Shard (10:52-11:15Z) and The Shard -> Canary Wharf (13:55-14:30Z). One earlier iteration still thrashed, which is what identified cause (2) | 11 Sep |
| Frontend boot screen | A returning signed-in visitor now sees a branded loading surface (spinner, status text, timeline skeleton, reduced-motion aware) instead of the marketing landing page flashing past before the day view. The hint is stored only after a live day loads, so first-time visitors still get the landing page instantly. Typecheck passes; the production bundle ships with the next frontend publish (this sandbox cannot run Vite/esbuild) | 11 Sep |
| Live proof, deployed stack | First live run `needs_input` in 20.7 s with two `Travel / Glide` blocks in the owner's primary calendar and one `unknown_start` decision; source appointments untouched. Repeat run: both receipts `unchanged`/`noop`, no duplicates, no false edits. Ten consecutive live sequences all terminal, 10.3-15.5 s (mean 11.3 s). Numbers in `docs/evaluation.md` | 11 Sep |
| Manual edit / deletion / pause | Moving a Glide block raised a `manual_edit` decision and left the block alone; deleting one raised `manually_deleted` and did not recreate it; a run while paused returned `paused` with no writes, and resume restored automation | 11 Sep |
| Scheduled run, terminal status | Task 1 resolved, and it needed a fix: the live workflow hard-coded `trigger="live"` when it persisted results, so every completed background run lost its `schedule` label. The trigger now flows from the job; after redeploy, a dispatcher invocation (`enqueued: 1`) produced a `trigger=schedule` run that reached terminal `needs_input` | 11 Sep |
| Timezone-canonical block hash | Repeat runs flagged Glide's own blocks as manually edited: `block_hash` hashed the raw UTC offset, so a block written in UTC and read back at `+01:00` produced a different hash. Times are canonicalised to UTC before hashing; the deployed repeat is now idempotent | 11 Sep |
| Disconnect (real Google) | `POST /api/auth/logout` returned `disconnected`, keeping one manually edited block with an explicit warning; settings flipped to paused; a later calendar read raised Google `RefreshError` (grant revoked); `/api/auth/status` reports `connected: false`, provider available. Run *after* the owner asked to skip it (the batch had already started), so the account needs one reconnect before recording | 11 Sep |
| Cleanup | Seeded fictional appointments deleted from the test calendar before the disconnect. One manually edited `Travel / Glide` block (12 Sep, 12:12-12:35 London) remains by design and needs a manual delete or a reconnect to clean up | 11 Sep |
| Alarms and queues at close | `glide-api-5xx`, `glide-api-throttles`, `glide-dlq-depth`, `glide-worker-errors` all `OK`; job queue and dead-letter queue both 0 visible / 0 in flight | 11 Sep |
| Deployed sample re-verified | `scripts/verify_deployed_sample.py` passes end to end after the deploys: 201 session, first check `needs_input` (1 block, 1 decision), move -> `completed` (2 blocks, 0 decisions), repeat -> 2 `unchanged` receipts, and the anonymous sample tenant is confirmed never scheduled. Its old step 5 waited for a scheduled *sample* run, which the design forbids, so it hung; that step now asserts the opposite | 11 Sep |
| Worker concurrency cap and turn budget codified | `infra/template.yaml` now sets `ScalingConfig.MaximumConcurrency=2` on the worker's SQS event, so the mitigation survives the next deploy, and `GLIDE_AGENT_TURNS: "24"` to match `DEFAULT_LIMITS["turns"]` in `strands_runner.py`. The template had still pinned `16`, and a deployed value beats the runner default, so every deploy since the agent-loop fix was reinstating the old budget. `scripts/validate_template.py` now fails on either drift and `tests/unit/test_template_guard.py` proves both checks fail closed. Offline on the current tree: `pytest` 329 passed, `ruff check .` clean, `validate_template.py` OK. Both values reach the account only with the next deploy | 11 Sep |
| Turn budget and concurrency cap now live | Verified against the account, not inferred: `glide-WorkerFunction-d6nb6fzzYV3a` reports `GLIDE_AGENT_TURNS: "24"`, `GLIDE_AGENT_MODE=bedrock`, a 200 s agent deadline and `LastModified` 2026-09-11T20:48:26Z; its event source mapping is `MaximumConcurrency=2`. CloudFormation's stored template already carries both values, so `sam deploy` from this tree answers **"No changes to deploy"** rather than shipping anything. The app-loop fix itself landed earlier in the evening: every run from 20:15Z onward is terminal `needs_input` with no `AgentProposalMissing`, including one `trigger=schedule` run at 20:38:18Z, where the four pre-fix runs from 19:44-19:45Z failed on the turn cap | 11 Sep |
| Outage bookkeeping left behind | Four run rows for the connected tenant from the outage window (19:49:59Z, 19:54:59Z, 19:59:29Z, 20:00:02Z) are still `queued` and will never move: their messages were archived and deleted from the dead-letter queue rather than reprocessed. The job queue holds 2 visible + 4 in flight, the dead-letter queue is empty, `glide-dlq-depth` is OK and `glide-worker-errors` is still in ALARM from its last 20:40Z datapoint. The tenant's settings are `enabled: false` (revision 7) after the disconnect, so the five-minute dispatcher (`ENABLED`) no longer enqueues for it; `/api/auth/status` answers `connected: false, provider_available: true` | 11 Sep |

Deployment fixes found and applied against the real account while validating:
SAM policy-template name, CloudFront/API circular dependency, CloudFront
disabled-caching policy rules (headers/cookies), Lambda `LoggingConfig`
log-group name format, account concurrency minimum, missing
`bedrock:InvokeModelWithResponseStream` permission, Strands' foundation-model
ARN resolution, and Mangum's API Gateway stage base path.

## In progress

- **Deployed worker reliability (mitigated, not yet shipped as code).** The
  Bedrock loop spends its whole 16-turn budget without an accepted proposal on
  the canonical sample day and then hits the 200 s deadline. The working tree
  routes anonymous sample sessions through the deterministic planner, which is
  the durable fix, but the deployed stack still runs the old code with the
  worker switched to `GLIDE_AGENT_MODE=deterministic` as a stopgap. That
  stopgap must be removed (`GLIDE_AGENT_MODE=bedrock`) once the new code is
  deployed so the live Google path stays model-backed.
- **Dispatcher schedule disabled.** It was adding a run every five minutes for
  every stored sample session, which is what fed the backlog. Re-enable it
  after the deterministic sample code is deployed so scheduled maintenance
  runs again; they are then sub-second and cheap.
- **Sandbox limits on git and frontend tooling.** This session cannot write
  inside `.git` (commits/pushes from the workspace fail with `Permission
  denied`) and cannot spawn esbuild, so the publication is mirrored through the
  GitHub API and the frontend gates are delegated to CI. Publishing must also
  enumerate intended *untracked* files by hand: the first snapshot missed
  `backend/glide/deploy/secrets.py` and CI failed with `ModuleNotFoundError`
  until it was added.
- **Concurrent workspace session.** Another session is actively editing the
  frontend, the screenshots, and separate security-audit files in this same
  checkout, and owns keeping the public repository in step with the shipped
  release. The published `main` at `20f7a770` is a verified snapshot, not
  necessarily the newest tree.

## Remaining

1. Reconnect the test account once (browser consent) so the demo video can
   show the live path, and delete the one leftover manually edited
   `Travel / Glide` block (12 Sep, 12:12-12:35 London) while connected.
2. Confirm the per-user token secret is actually removed after a disconnect
   (`glide/tokens/*` still listed immediately after the call; the grant itself
   was already invalid, so treat this as a cleanup-hygiene check) and confirm
   a refreshed access token is persisted back to Secrets Manager.
3. Keep the public repository in step with the shipped release; the
   concurrent session owns that synchronisation from here on. The OAuth,
   agent-loop, trigger-label, block-hash, and frontend changes above are
   deployed or staged but not yet mirrored publicly, and the frontend bundle
   still needs one CI publish.
4. Finalize submission assets against the shipped release (story, architecture
   export, screenshots, video materials, fields), then the owner uploads the
   video, enters the Builder identifier, confirms eligibility, and submits.
5. Keep the hosted judge experience available through 9 October 2026 and
   monitor gross usage against the USD 75 ceiling.
8. Clear the FIFO jam measured on 11 September: the failing live job blocks
   its per-user message group, leaving later scheduled runs `queued`. After
   the agent-loop fix is deployed, confirm the group drains, the four stuck
   runs reach a terminal status, and the DLQ returns to zero.
9. Enable decision notifications: verify one SES identity in `eu-west-1`,
   redeploy with `-NotificationFromEmail`, set the address for the connected
   tenant (it is currently null), and capture delivery evidence. Steps are in
   [notifications-next-steps.md](notifications-next-steps.md).
10. ~~Codify the worker `ScalingConfig.MaximumConcurrency=2` cap in
    `infra/template.yaml`~~ **Done in the working tree, not yet deployed.**
    The cap is on the template's worker queue event and the offline validator
    fails if it disappears.
11. ~~Deploy the two template values that are staged but not live~~ **Done
    11 Sep 20:48Z.** The live worker reports `GLIDE_AGENT_TURNS: "24"` plus the
    `MaximumConcurrency=2` event source mapping, and the stack template already
    matches this tree, so a `sam deploy` now answers "No changes to deploy".
    What is left from the outage is bookkeeping, not behaviour: the four
    `queued` run rows from 19:49-20:00Z can never reach a terminal state because
    their messages were purged from the dead-letter queue, so leave them or
    clear them deliberately rather than expecting a worker to pick them up.

## Owner action items

- Run `aws login` (or `aws login --profile glide`) again when asked: the root
  sign-in session is short-lived and the refresh fails from this sandbox, so
  each remaining deploy and verification batch needs a fresh login. Tell the
  agent as soon as it completes so the token can be copied into a writable
  home before it rotates.
- Verify one Amazon SES identity in `eu-west-1` (fastest: your own address,
  used as both the From identity and the Google test account's inbox). The
  account is in the SES sandbox, so unverified recipients will not receive
  mail.
- Later: upload the video, enter the AWS Builder identifier, confirm
  eligibility, and press Submit on Devpost.

## Continuation notes (11 September, ~20:25 BST)

Deployed state, all verified this session:

- Stack `glide` is `UPDATE_COMPLETE`. The new code is live: runtime Secrets
  Manager lookups (`GLIDE_SESSION_SECRET_ARN`, `GOOGLE_CLIENT_SECRET_ARN`),
  API access logging with `DefaultRouteSettings` throttling (burst 50 /
  rate 25), and the deterministic planner for anonymous sample sessions while
  live tenants stay Bedrock-backed.
- Worker is on `GLIDE_AGENT_MODE=bedrock`, its SQS event source mapping is
  capped at `MaximumConcurrency=2`, and `DispatcherFunctionSchedule` is
  `ENABLED` (it fired at 20:18 and the worker ran three invocations).
- Sample pipeline on the new code: first check `needs_input` with one block and
  one shortfall decision (7.5 s cold, 2.1 s warm), move gives two blocks and no
  decisions, repeat is idempotent (all receipts `unchanged`, 0.4 s).
- Job queue empty. The dead-letter queue still holds 74 pre-fix failures, so
  `glide-dlq-depth` is in `ALARM` until those messages are purged.

Outstanding:

1. Confirm a `trigger=schedule` run row reached a terminal status (the
   dispatcher and worker both ran at 20:18, but `/api/day.last_run` does not
   expose scheduled runs; check CloudWatch logs or the DynamoDB run rows).
2. Owner completes Google consent, then verify connect, refresh/reconnect,
   idempotency, manual-edit and deletion respect, pause, and disconnect against
   the real calendar and record ten live maintenance sequences.
3. Finalize submission assets; owner uploads the video, enters the Builder
   identifier, confirms eligibility, and submits.

Operational notes for the next session:

- AWS sign-in sessions are short-lived and a refresh from this sandbox fails
  with `invalid_grant` because the CLI cannot persist the rotated token under
  `~/.aws/login`. After `aws login`, immediately copy `C:\Users\harve\.aws`
  to a writable temp directory and run AWS/SAM with `USERPROFILE` and `HOME`
  pointing at that copy (and `APPDATA` at a temp SAM directory); refreshes then
  persist and the session survives.
- Clear `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` before any network call. Python
  `requests` and `gh` work; git's HTTPS transport and `curl.exe` do not.
- The sandbox cannot write inside `.git` or spawn esbuild, so local
  `npm run build`/Playwright are unavailable (CI covers them), and
  `sam validate` needs `APPDATA` redirected.
- `infra/template.yaml` must not set `RouteSettings` keyed by an endpoint path:
  the API is a single `ANY /{proxy+}` route and that mistake fails the stage
  update into `UPDATE_ROLLBACK_FAILED`. Recovery is
  `continue-update-rollback --resources-to-skip HttpApiStage` then a normal
  re-deploy.
- `sam deploy` parameters: `Stage=prod`,
  `BedrockModelId=eu.amazon.nova-2-lite-v1:0`,
  `GoogleClientId=$env:GOOGLE_CLIENT_ID`,
  `GoogleClientSecretArn=arn:aws:secretsmanager:eu-west-1:111122223333:secret:glide/google-client-secret-kcy2ky`,
  `FrontendOrigin=https://d3tvxy281s2u11.cloudfront.net`, plus
  `--capabilities CAPABILITY_IAM --resolve-s3 --no-confirm-changeset`.
- The concurrent session owns keeping the public repository in step with the
  shipped release.

## Deployed-state verification pass (11 September, ~21:00 BST)

Read-only checks against the account, the live site, the public repository,
and the current tree. Everything below was observed directly, not inferred
from earlier notes.

| Check | Observation | Date |
| --- | --- | --- |
| Stack | `glide` is `UPDATE_COMPLETE`; last update 2026-09-11T19:58Z; parameters include `NotificationFromEmail=""` | 11 Sep |
| Live site | `/api/health` returns `{"status":"ok","mode":"sample","version":"0.1.0"}` | 11 Sep |
| Lambda config | API has `GOOGLE_REDIRECT_URI=https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback`, `GLIDE_ENV=production`; worker has `GLIDE_AGENT_MODE=bedrock`, `BEDROCK_MODEL_ID=eu.amazon.nova-2-lite-v1:0`, `GLIDE_PUBLIC_BASE_URL`, and an empty `GLIDE_NOTIFICATION_FROM`; no reserved concurrency on any function | 11 Sep |
| Guardrails | `DispatcherFunctionSchedule` ENABLED (`rate(5 minutes)`); worker event source mapping capped at `MaximumConcurrency=2` (live only, not in the template); template has API throttling (burst 50 / rate 25) and 30-day log retention; **no WAF** | 11 Sep |
| Alarms and queues | `glide-worker-errors` **ALARM** since 19:46Z; `glide-api-5xx`, `glide-api-throttles`, `glide-dlq-depth` OK; job queue 3 visible + 1 in flight; DLQ 1 message | 11 Sep |
| Connected Google tenant | `google:<subject>`, `enabled: true`, `revision: 2`, `notification_email: null`; six runs (19:44–20:00Z): two `failed` with `AgentProposalMissing`, four `queued`; no blocks, decisions, or receipts | 11 Sep |
| Live failure cause | Worker log: `agent stop_reason=<limit_turns>` twice, then `AgentProposalMissing: invalid_journey` (`strands_runner.py:388`); ~45 s per attempt | 11 Sep |
| Sample path | Deployed sample runs at 19:19–19:33Z reached `needs_input`/`completed` with the expected counts; the deterministic planner is live for anonymous tenants | 11 Sep |
| SES | Sending enabled, **production access off** (sandbox), **zero verified identities** | 11 Sep |
| Repository | `github.com/harveybellini/glide` loads signed out, licence MIT; `main` = `20f7a770`, so the notification commit `2077f6e` and the agent-loop fix are not pushed | 11 Sep |
| Current tree | `pytest` 323 passed, `ruff` clean, template OK, frontend typecheck and production build green (322 at the time of the pass; the tree is still changing) | 11 Sep |

Corrections to earlier notes: the owner's Google consent is **done** (a live
tenant exists), so N7 is blocked by the planner, not by access; the overnight
pause has been lifted; and the worker concurrency cap is not part of the
template.

## Decision notifications (11 September, offline)

Added the "only surfaces when a real decision needs making" half of the
hackathon theme. Verified in this workspace:

| Checkpoint | Evidence | Date |
| --- | --- | --- |
| Notification policy | `Decision.notified_at` dedupe mark; `carry_notification_state` re-applies it to the fresh decisions every run rebuilds; a failed SES send stays unmarked and is retried by the next check (`backend/glide/domain/notifications.py`) | 11 Sep |
| SES transport | `SesDecisionNotifier` sends one `sesv2.SendEmail` per decision with plain-text and HTML bodies, an escaped/URL-encoded deep link (`/?decision=<id>`), and optional configuration set (`backend/glide/adapters/notifications.py`) | 11 Sep |
| Settings surface | `notification_email` (defaults to the Google sign-in address) and `notify_on_decisions` on `UserSettings`; `PATCH /api/settings` accepts, clears, and validates them, and rejects them for anonymous sample sessions with a clear message | 11 Sep |
| UI | Settings panel shows the decision-email field and toggle for signed-in users; `?decision=<id>` scrolls to and outlines the matching card | 11 Sep |
| Stack | `NotificationFromEmail` parameter, `AWS::SES::EmailIdentity` (conditional), worker `GLIDE_NOTIFICATION_FROM` / `GLIDE_PUBLIC_BASE_URL`, and `ses:SendEmail` scoped to that identity; `deploy.ps1` forwards the parameter | 11 Sep |
| Tests | `pytest`: 322 passed (including a processor-level once-only test); `ruff check .`: clean; `scripts/validate_template.py`: OK; frontend `tsc -b`: clean; `docs/openapi.json` regenerated | 11 Sep |

Not yet done (needs the owner's account access): verify a sending identity in
SES, deploy with `-NotificationFromEmail`, and record the live delivery
evidence in `docs/live-proof-runbook.md` step 5. The `glide` profile's AWS
session has to be refreshed with `aws login` first.
