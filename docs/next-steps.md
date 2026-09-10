# Glide — implementation review and completion steps

Reviewed 9 September 2026 against `plan.md`, at repository commit `ef0c239`.

## Assessment

Glide has a working local sample application and substantial backend implementation. The browser workflow, deterministic scheduling, typed Strands tool host, provider adapters, persistent storage adapters, and submission drafts are useful foundations.

It is **not yet ready for real-calendar use or deployment**. Credentials are only one dependency: live account wiring, provider compatibility, calendar mutation safeguards, and deployed job processing require code changes. A passing fixture test does not demonstrate that Google or AWS accepts the corresponding request.

This review changed documentation only. No real accounts, billable providers, deployment, publication, or personal calendar data were used. Offline probes used synthetic fixtures and the installed SDKs. The original implementation and original plan remain intact.

## Checks performed

| Check | Observed result | Interpretation |
| --- | --- | --- |
| `uv run pytest` | **120 passed, 3 failed; 123 collected** | Three live-processor tests depend on the current date/time; details below. |
| `uv run ruff check .` | Passed | Python lint is clean. |
| Frontend `npm run typecheck` | Passed | TypeScript checks pass. |
| Frontend `npm run build` | Passed after rerunning outside the initial process sandbox | The initial esbuild `EPERM` was environmental, not a build defect. |
| `uv run python scripts/validate_template.py` | Passed | This checks selected structure and handler file presence, not AWS deployability. |
| Frontend `npm run e2e -- e2e/judge-path.spec.ts` | **4 passed** with both API and Vite running | Covers sample conflict/edit/recheck/reset, skip, settings controls, and a keyboard accessibility path. |
| Actual SAM validation/build/deploy | Not run; SAM was not found on the current command path | Cloud/runtime acceptance is unverified. |
| Real Google, Amazon Location, Bedrock | Not run | No live proof or live latency/cost measurements. |
| Git publication | Two local commits; no remote configured | No published repository was verified. |

The first browser attempt failed because the API server was absent. With the correct local servers and an isolated test database, all four tests passed. Servers were stopped and the temporary database was removed. Those initial failures should not be reported as four application defects.

## Confirmed findings to fix

References below identify the reviewed version; line numbers may move during implementation. Backend paths such as `api/auth.py` are relative to `backend/glide/`; other paths are relative to the repository root.

| ID | Priority | Evidence | Consequence |
| --- | --- | --- | --- |
| F1 | P0 | `api/auth.py:275` exchanges credentials but retains only identity in an `AuthSession`. `api/deps.py:27` and `api/routes/demo.py:41` authenticate application data through sample-session headers. | Google sign-in is not connected to live settings, token persistence, calendar data, or user-triggered live jobs. |
| F2 | P0 | `api/auth.py:148` creates a PKCE challenge; `:158` constructs a fresh flow without retaining its verifier. `:221` stores state only in process memory. | The current OAuth flow loses required transaction context and will not reliably survive a redirect across Lambda instances. |
| F3 | P0 | Offline two-browser probe: a state created in one test client was accepted in another. The start/callback routes have no initiating-browser binding. | OAuth state is single-use but not bound to the browser initiating authorization. Complete that protection before enabling real accounts. |
| F4 | P0 | `adapters/google_calendar.py:285` and `:311` call `.execute(headers=...)`. Installed `HttpRequest.execute` accepts `(self, http=None, num_retries=0)`. | Real update/delete calls fail before reaching Google, despite permissive fake requests passing tests. |
| F5 | P0 | `adapters/google_calendar.py:160` ignores the saved calendar ID, lists calendars using an unrequested scope, and selects by title. | Calendar discovery can fail authorization or select an unrelated same-named calendar; it also ignores pagination. |
| F6 | P0 | `adapters/google_calendar.py:173` creates event bodies without a deterministic provider ID. | The requested insert-timeout/uncertain-success retry contract is not implemented. Listing and reconciling later is helpful but is not the planned insert deduplication guarantee. |
| F7 | P0 | Offline probe of `domain/live.py:140`: a manually edited travel block was deleted when its source disappeared. The other removal branch at `:183` also lacks a manual-override guard. | The current executor can remove travel blocks that the user edited. |
| F8 | P0 | Offline three-run probe of `domain/live.py:344`: manual deletion creates a decision on run 2, but run 3 recreates the block after the previous-block record disappears. | Manual deletion is not durably respected; a persisted skip/tombstone is needed. |
| F9 | P0 | `deploy/worker.py:36` constructs `DemoSessionStore` without its DynamoDB store. A cold-worker probe failed to restore an existing persisted sample. | The deployed public sample cannot complete on a separate fresh worker instance. |
| F10 | P0 | `deploy/dispatcher.py:42` enqueues scheduled work without a queued run row. `api/run_service.py:38` discards results when that row is absent. Reproduced offline. | Even after fixing F9, scheduled sample results can be discarded. |
| F11 | P0 | `api/demo_store.py:153` and `:160` return warm cached sessions without checking persisted revisions. Reproduced with two stores sharing one fake DynamoDB. | A worker can keep using an old appointment, skip, reset generation, or setting after another instance changes it. |
| F12 | P0 | `deploy/api.py:17` imports `api/app.py`, whose module-level `app = create_app()` at `:145` first initializes SQLite. An import probe confirmed this side effect. | The deployed API bootstrap attempts local persistence before selecting DynamoDB; it must not depend on a writable Lambda code directory. |
| F13 | P0 | `infra/template.yaml:260` explicitly sets reserved `AWS_REGION`; API variables omit `GOOGLE_REDIRECT_URI`; worker lacks access to its per-user token secrets; origin policy forwards all viewer headers at `:100`. | The template has configuration/permission/routing problems beyond its structural checker. See task N6. |
| F14 | P1, required before release | `tests/unit/test_live_processor.py:24` fixes fixtures to 2026-09-09, while `live/processor.py:40` uses current time. | Tests at lines 84, 108, and 147 fail once those appointments leave the active window. Inject/freeze a clock; do not merely replace the date. |

Additional review concerns are included in the relevant tasks: production fencing is not implemented by the constant `lease_revision=1`; source/settings changes need validation at commit time; disconnect is not wired to the UI; sample sessions lack enforced expiration; production agent selection can fall back to deterministic planning; and the UI waits only ten seconds for work allowed to take 120 seconds.

## Ordered implementation tasks

Keep Google Calendar and driving. Defer walking, public transport, maps, additional calendars, push notifications, and AgentCore until the existing path passes. Follow AGENTS.md: one write-capable worker at a time; lead owns auth, concurrency, safety, debugging, and final review.

### N0 — prepare account access alongside the code work

**Owner: user. Dependency: none.**

- Confirm an AWS account/profile, a cash spending cap, and whether hackathon credits were requested/received.
- Provide access to a tool-capable Bedrock model and Amazon Location in the chosen region; record the tested model ID.
- Configure Google Cloud Calendar API, an OAuth web client, the owner's test-user account, and exact local/deployed callback URLs.
- Use ignored local configuration and secret storage, not chat or committed files, for credentials.
- Verify AWS Builder ID, entrant/team details, repository owner, and video-host account for later publication. This review did not inspect private identifiers.

**Done when:** the implementer can run narrowly scoped live tests with the owner's intended account and budget. Most fixes below can begin before this is complete. Use `docs/live-proof-runbook.md` after correcting its script dependencies.

### N1 — restore a reliable test baseline

**Owner: lead, with test_runner for execution. Files: `live/processor.py`, `tests/unit/test_live_processor.py`, test setup/CI if needed.**

- Introduce a clock dependency or freeze time in processor tests so they work on any day and time zone.
- Add regression coverage for the confirmed defects as each task fixes them. In particular, use actual Google `HttpRequest` behavior or a strict fake, not a fake that accepts unsupported keyword arguments.
- Keep browser setup reproducible: start both servers or configure Playwright to manage them, using an isolated test database and no live providers.
- Test actual deployment factories/handlers, not just a local app manually supplied with fake deployed adapters.

**Done when:** all current Python tests pass independent of wall-clock date; four browser tests, lint, typecheck, and build pass; added regressions fail before their corresponding fix and pass afterward.

### N2 — complete Google connection and live application wiring

**Owner: lead. Files: `api/auth.py`, `api/app.py`, `api/deps.py`, API routes/schemas, credential storage, `frontend/src/api.ts` and connection/settings screens.**

- Persist an expiring OAuth transaction containing state, initiating-browser binding, and the PKCE verifier; consume it once using shared storage. Validate callback/identity/scopes and denied/replayed/expired attempts.
- Store returned access/refresh credentials securely, including expiry and an update path after refresh. Create or load the authenticated user's settings and connection record.
- Distinguish sample and live users in the API and UI. Wire live day/settings/runs/decisions/activity to the authenticated server identity and shared job queue. Keep sample editing confined to synthetic data.
- Add real starting-address search/confirmation, time-zone/earliest-departure preferences, and explicit enablement of live automation. Current settings choices are fictional fixture places.
- Expose pause and real disconnect. `ConnectionStatus` currently calls logout; clearing a cookie is not disconnecting the Google grant or stopping background work.
- Wire `DisconnectService` and a real revocation transport. The current credential store has no save operation and only performs revocation HTTP when an injected transport exists. Preserve sufficient token data to finish revocation and report failures accurately.
- Enforce ownership, CSRF/origin checks for authenticated mutations, secure cookies, and current policy revision before writing. Use safe per-user secret identifiers and matching IAM scope.

**Done when:** a Google test user can connect, confirm preferences, view their own events, enable a job, answer a decision, refresh/reconnect, pause, and disconnect across restarts. A second user cannot read/change their data; OAuth callbacks cannot move between browsers or be replayed.

### N3 — make the Google adapter compatible with the real SDK

**Owner: focused integration worker; lead reviews permissions and mutation behavior. Files: `adapters/google_calendar.py`, focused adapter tests, connection settings persistence. Depends on N2 contracts.**

- Put `If-Match` on the request's headers before calling `.execute()`. Test real request object behavior and 404/409/412/provider failure mapping. The [official HttpRequest API](https://googleapis.github.io/google-api-python-client/docs/epy/googleapiclient.http.HttpRequest-class.html) also documents the supported execute arguments.
- Use the saved app-created calendar ID and verify the ownership relationship. Do not discover/adopt calendars solely by summary. Persist the ID returned on creation, which `LiveRunProcessor` currently does not write back to settings.
- Resolve the scope mismatch without silently requesting full calendar control. `calendarList.list` does not accept the currently requested event-read/app-created scopes. Prefer the saved-calendar design; if list access is needed, explicitly add the narrow list scope and handle all pages. [Google calendar-list authorization](https://developers.google.com/workspace/calendar/api/v3/reference/calendarList/list)
- Implement deterministic Google-compatible event IDs and recovery after uncertain insert success. Validate fetched private markers against the user, calendar, and stored mapping before adopting or mutating a block.
- Preserve ownership/hash/revision fields consistently when fetching blocks; review `get_block`, which currently replaces several of them with `unknown` defaults.

**Done when:** actual Google create/read/update/delete, same-name calendar separation, reconnect/reuse, recurring-instance changes, no-op repeat, and timeout/retry checks pass without touching original appointments.

### N4 — finish mutation safety and durable reconciliation

**Owner: lead. Files: `domain/live.py`, `live/processor.py`, `live/disconnect.py`, state adapters/interfaces, job coordination, corresponding tests.**

- Preserve manually edited blocks on every deletion/update path, including removed source events and infeasible journeys. Present a decision instead of deleting them.
- Persist manual-deletion skips/tombstones across runs, refreshes, and worker restarts. Reopen only according to a defined source-revision rule.
- Apply the future/not-started guard to every mutation, including disconnect cleanup; current cleanup iterates all returned blocks, including the lookback interval.
- Verify both application mapping and provider ownership markers. Do not accept another user's block solely because it has a Glide marker.
- Add shared lease/fencing and settings/source revision checks around writes and persisted results. A fixed `lease_revision=1` and queue ordering do not protect against stale workers, pause/reset changes, or redelivery after a timeout.
- Persist mutation intent/receipts and recover partial success. The current processor saves a whole result only after external writes finish, so a crash can leave Google ahead of database history.
- Carry persisted live decisions, confirmed place choices, and skips into `LiveRunProcessor`; currently it only resolves fresh location searches and does not pass stored skips to the workflow.

**Done when:** two-worker/stale-policy tests, crash-after-first-write, manual-edit removal, three-run manual deletion, past/started travel, and disconnect tests all pass. No stale worker or later poll reverses a user's choice.

### N5 — repair deployed sample processing and background jobs

**Owner: lead. Files: `deploy/api.py`, `api/app.py`, `deploy/worker.py`, `deploy/dispatcher.py`, `api/demo_store.py`, `api/run_service.py`, state/queue adapters.**

- Remove import-time local SQLite initialization from the deployed API path. Keep a local entrypoint if needed, but deployment must choose its storage before constructing an app.
- Supply the shared DynamoDB store to the worker's `DemoSessionStore`.
- Create queued run records for scheduled jobs, or replace the missing-row heuristic with an explicit persisted generation/tombstone design. Preserve reset protection while allowing legitimate scheduled work.
- Reload/version-check sample snapshots and settings on each job/read. A warm object's cache must not outrank a newer stored appointment, skip, reset, or pause.
- Fence result commits by tenant generation/settings revision. Add explicit safe failed-run status for live worker exceptions so the UI cannot stay queued indefinitely.
- Enforce session expiry during access and dispatch. The current sample `created_at` is not an access check, and restoring a session gives it a fresh in-memory timestamp.
- Bound dispatcher work and cost; a paginated loop over every settings row is not a bounded total invocation. Ensure expired/inactive tenants stop generating model calls.

**Done when:** use separate API, dispatcher, and worker instances against shared storage. Create a session in one, run it in another, edit/pause/reset in the first, then reuse the worker. Each action sees current state; scheduled results appear after the browser closes; expired sessions cannot run.

### N6 — validate and deploy the actual AWS package

**Owner: lead; test_runner for SAM/build checks. Files: `infra/template.yaml`, `scripts/deploy.ps1`, packaging/CI, deployment configuration. Depends on N5 and account/budget access.**

- Remove the explicit `AWS_REGION` environment assignment: Lambda reserves it. Let the runtime provide it. [Lambda environment variables](https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html)
- Provide the exact deployed `GOOGLE_REDIRECT_URI`; its absence currently makes `GoogleOAuthConfig.from_env()` select the unavailable provider.
- Give only the required components access to per-user token secrets and encryption keys. Check Bedrock permissions for the actual streaming/non-streaming invocation used by the installed SDK, and scope model resources.
- Use an origin request policy suitable for API Gateway instead of forwarding the viewer's `Host` header. Use a validated no-cache policy for API responses and retain necessary cookie/query/session headers. [CloudFront API Gateway origin guidance](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html)
- Run real `sam validate --lint` and `sam build`, including dependency-cycle, resource-schema, IAM, ARM64 dependency, and packaging checks. The current custom checker is insufficient.
- Make the PowerShell deploy script stop on native command failures and validate stack outputs before uploading. Use safe secret parameters/configuration; inspect the concrete deployment before provisioning.
- Set log retention, alarms, concurrency/request limits, and usage monitoring. Account for judge access through October, including expired anonymous sessions.

**Done when:** HTTPS `/api/health`, sample creation, authenticated callback, worker execution, and scheduled updates work through CloudFront from a fresh browser. Confirm cold starts, logs without secrets, and the live model's actual invocation. Do not call a successful YAML parse a deployment test.

### N7 — obtain real provider evidence and finish the user flow

**Owner: integration/UI workers sequentially; lead integrates. Files: `scripts/live_smoke.py`, `agent/strands_runner.py`, live processor/place flow, frontend settings/decision/run states, live runbook.**

- Correct the live smoke script: it currently asks one place query for two candidates, then passes fictional `place_index(events)` IDs to a real router. Resolve two intentional real venues separately, then construct source events/place references using real provider IDs.
- Run a real route, real Bedrock/Strands tool sequence, and Google calendar maintenance loop. Record actual request outcomes and durations, not fixture expectations.
- Require explicit Bedrock success for the submitted agent path. Current runner selection can silently fall back to deterministic planning when configuration fails; retain that mode for local development but label it and avoid claiming model execution.
- Resolve ambiguous places through a real UI decision/confirmation path, with provider-permitted persistence. Make a user-supplied start point work end to end.
- Extend the UI's ten-second run wait to support the 120-second backend budget, queue delays, progress, reload recovery, and actionable failed/expired states.
- Run ten integrated maintenance sequences, including change/cancel/retry and at least one scheduled run with the browser closed. Record counts, failures, latency, and cost separately for fixtures and real providers.

**Done when:** the real app completes connect → source read → timed route → Strands proposal → safe calendar block → source change → update/delete → conflict decision → resolution. Two unfamiliar testers can try the sample and explain the decision flow.

### N8 — finish the public submission package

**Owner: documentation worker, then lead and user for actual publication/submission. Files: `README.md`, `docs/`, `submission/`, public repository/assets. Depends on verified release behavior.**

- Publish the repository to the intended GitHub/GitLab/Bitbucket account; verify signed-out source/setup/license access and run CI. No remote is currently configured.
- Update evidence counts and claims. `docs/architecture.md` says 94 tests; evaluation/decision notes say 122; this review collected 123 with three failing. Preserve dates rather than implying all historical counts are current.
- Correct `submission/screenshots/README.md`, which says there are no images despite four PNGs. Keep local captures labeled and recapture the final deployed release as needed.
- Fill accomplishments, actual challenges, learnings, and actual model/hosting details in the story. Remove unsupported live/AgentCore claims. Update the architecture diagram to the deployed system.
- Record and publicly upload a video of at most five minutes using the real-provider workflow and actual values. No video publication was verified during this review.
- Fill repository/demo/video URLs, AWS Builder ID, entrant/team/eligibility fields, testing instructions, and gallery selections. Verify each from a fresh browser.
- Confirm image/diagram formats and size limits, and confirm the MIT license is detected in repository About. Publish the optional AWS Builder article only after required deliverables are complete.
- Submit Devpost, save the receipt, and preserve an identifiable submitted code revision and working judge experience.

**Done when:** all required fields/artifacts are real, public where required, consistent with the shipped release, and the entry is confirmed submitted rather than saved as a draft.

## Suggested remaining schedule

| Date, UK time | Finish |
| --- | --- |
| 9–10 September | N0 access setup; N1 baseline; N2/N3 live connection and SDK fixes; begin N4 safety regressions. |
| 11 September | Complete N4/N5 and first real provider proof; fix N6 template/package. Request credits before 20:00 BST if desired. |
| 12 September | Deploy, run separate-instance/background checks, finish N7 decisions/UI, and run integrated evaluation. Freeze optional features. |
| 13 September | Usability fixes, final evidence, story/diagram/screenshots, complete first recording. |
| 14 September by 18:00 BST | Publish verified assets and submit; retain a seven-hour contingency. |

The official deadline remains **15 September 2026, 01:00 BST** (14 September, 17:00 PDT). Working judge access is required through the end of judging, **9 October, 01:00 BST**. Rechecked on 9 September. [Official rules](https://agentsforhumans.devpost.com/rules)

## Progress since the review (9 September working session)

All offline-verifiable fixes through N5 are implemented and tested (266 tests,
ruff, frontend typecheck/build, 4 Playwright checks). Real-account and
deployment milestones remain blocked on N0 account access.

| ID | Status |
| --- | --- |
| F1 | Fixed: one API surface now serves sample sessions (header) and signed-in Google users (encrypted cookie), with per-user settings/day/runs/decisions/activity, pause/resume, places search, and tenant isolation tests. |
| F2 | Fixed: OAuth transaction (state + PKCE verifier, 10-minute expiry) is encrypted in an HttpOnly, path-scoped cookie and consumed once. |
| F3 | Fixed: the transaction cookie binds the callback to the initiating browser; a foreign-browser or replayed callback is rejected. |
| F4 | Fixed and checked against the official Calendar guide: `If-Match` is set on `HttpRequest.headers` before `execute()`; 404/409/412 map to the named errors. |
| F5 | Superseded by the 10 September requirement: app-owned travel blocks are written to the user's primary calendar with `calendar.events.owned`, not a separate calendar. |
| F6 | Fixed: deterministic event ids derived from journey + source revision (base32hex subset), with 409 recovery validating owner/journey/revision. |
| F7 | Fixed: manual-override guard on removal paths and disconnect cleanup; past/started blocks are never modified. |
| F8 | Fixed: resolved skips persist across runs and reopen only when the source revision changes; open `manually_deleted` decisions also survive restarts. |
| F9 | Fixed: the worker builds `DemoSessionStore` with the shared DynamoDB store. |
| F10 | Fixed: the dispatcher writes a queued run row before enqueueing scheduled jobs. |
| F11 | Fixed: reads and dispatch restore persisted snapshots/settings instead of trusting warm caches. |
| F12 | Fixed: production import is gated by `GLIDE_ENV=production` (set in the template) and verified to build no local SQLite app. |
| F13 | Fixed: reserved `AWS_REGION` removed, `GOOGLE_REDIRECT_URI` added, worker token-secret access added, API geo-places search added, CloudFront origin policy switched to `AllViewerExceptHostHeader`, log retention and concurrency limits added. |
| F14 | Fixed: processor and disconnect take an injected clock; tests freeze time. |

Added beyond the review: refresh-token persistence after credential refresh,
settings-revision fencing so a run cannot commit a superseded policy, explicit
`LiveProcessorUnavailable` failures locally, a production fail-fast for Bedrock
configuration (no silent deterministic fallback), and a corrected
`scripts/live_smoke.py` that resolves two real venues separately before real
routing/model calls. The 10 September requirement (primary-calendar writes)
was then applied across OAuth scopes, the Google adapter, reconciliation,
the UI, tests, and documentation.

Account access progressed on 10 September: the `glide` profile authenticates,
Bedrock `eu.amazon.nova-2-lite-v1:0` and Amazon Location Places/Routes work
in `eu-west-1`, and the OAuth client configuration is saved locally. Still
pending, in order: finish **N6** deployment (the template now passes
`sam validate --lint`; `scripts/build_lambda.ps1` produces the Linux bundle;
the first deploy run was interrupted and must be retried), then **N7** Google
primary-calendar proof and ten maintenance sequences, and **N8** repository
publication, Devpost fields/video/URLs, and submission. Recapture the gallery
screenshots against the deployed release; the four committed PNGs are local
sample captures only.

## Handoff instruction

> Implement the fixes and completion tasks in `docs/next-steps.md`, preserving Glide, Google Calendar, and driving. Follow the existing plan's interfaces where valid, but treat the reproduced findings in this review as corrections to completion claims. Start with N1–N5 using offline regressions while the owner prepares N0. The lead owns auth, concurrency, safety, and final review; run only one write-capable worker at a time. Complete real integration and deployed-instance proof before claiming submission readiness. Record actual checks and unresolved account dependencies, and finish N8 against the shipped release.
