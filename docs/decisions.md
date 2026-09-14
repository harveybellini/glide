# Implementation decisions

Planning date: 8 September 2026.

## 8 September

- License set to MIT in `pyproject.toml` and `LICENSE`, matching the plan.
- `padding_minutes` bounded to 0-60 to match the plan.
- First checked-in workflow uses in-memory synthetic calendar and route
  adapters. These are labeled fixtures and are not evidence of live provider
  integration.
- The sample starts at location A, so no artificial home-to-A block is created.
  When `start_place` differs from the first physical event, the same scheduling
  path plans a start-address journey.
- `tzdata` was added as a runtime dependency so Windows and minimal containers
  can resolve IANA time zones.
- The local web sample uses a fixture-backed FastAPI service with isolated
  session IDs and a React/Vite interface. Sample tenants have no access to live
  credentials.
- The domain executor takes an `AgentRunner` boundary and defaults to a
  deterministic planner. A Strands-backed runner can replace it without
  changing reconciliation or write policy.
- SQLite provides the local state adapter. It stores JSON payloads from the
  frozen domain models so the DynamoDB adapter can reuse the same contracts.
- Google OAuth now has a local-first auth boundary: one-time server state,
  HttpOnly SameSite cookies, identity-token issuer/audience validation, and a
  scope check. When credentials are absent the same routes return a clean 503
  rather than exposing a broken provider path. Live exchange is verified end
  to end (11 September 2026), including stored and refreshed refresh tokens.
- Amazon Location Places and Routes V2 adapters now map ``SearchText`` and
  ``CalculateRoutes`` onto the provider contracts. Coordinates use the wire
  order ``[longitude, latitude]``; places mark ``STORAGE_ALLOWED`` only when
  ``IntendedUse=Storage`` was requested; route geometry is never persisted.
  The adapter treats an empty ``Routes`` list, a missing duration, or
  route-unavailable wording inside a ``ValidationException`` as no-route. The
  exact live no-route error shape still needs confirmation on a real account.
- The demo API's ``POST /api/runs`` now enqueues work instead of running it
  synchronously. A local worker mirrors the planned SQS FIFO batch-size-one,
  lease-and-retry shape, and complete results commit atomically to SQLite so
  polling can never observe a terminal run with partial child records. Reset
  supersedes queued jobs and clears the tenant's persisted rows. A run ID
  collision with the previous numeric suffix scheme was avoided by using
  random run IDs and insertion-order ``last_result`` lookup.
- The Strands-backed ``StrandsAgentRunner`` wires the six plan-mandated tools
  (`read_schedule`, `lookup_place`, `estimate_journey`, `evaluate_candidate`,
  `request_decision`, `propose_plan`) around a deterministic ``ToolHost``.
  `propose_plan` is a regular validated tool rather than the SDK's
  structured-output tool so the plan's exact tool name and the single
  application-level repair retry stay under application control.
  The host owns every identity, budget, and arithmetic result, accepts only
  proposals that cover the server-supplied journey pairs with known
  references, and gives the model one schema-repair retry. Per-run bounds are
  ten model turns and a 120-second deadline, and the schedule/tool budgets
  are configurable. The canonical fixture day materializes journey plans
  identical to the deterministic planner. Sample mode selects the runner
  through ``GLIDE_AGENT_MODE=bedrock`` and falls back to the deterministic
  runner when ``BEDROCK_MODEL_ID`` is absent, so no live call is ever made
  without explicit configuration.
- The sample UI gained the W4 surface: editable source appointments (time and
  location, with a blank location producing an unresolved-location decision),
  a travel-settings panel (arrival buffer and earliest departure),
  pause/resume, and an honest Google connection status backed by a new
  ``GET /api/auth/status`` endpoint that reports connectivity and provider
  availability without leaking tokens. ``PATCH /api/settings`` became
  truly partial (each field optional), and the UI added a skip link,
  aria-live status, focus-visible styling, and reduced-motion handling.
- The deployment skeleton landed: a SAM stack (`infra/template.yaml`) with a
  private S3/CloudFront origin, Mangum API Lambda, FIFO queue with DLQ,
  worker and five-minute dispatcher Lambdas, and one on-demand DynamoDB table
  (`pk`/`sk`, `user-index` GSI, TTL on receipts). ``DynamoDbStateStore``
  mirrors the SQLite contract with transactionally atomic ``save_result`` and
  never deletes-and-puts the same block in one transaction (an idempotent
  rerun stays a single write). ``SqsJobQueue`` implements enqueue with
  per-user FIFO groups; supersede is a documented no-op because the run
  ownership check already fences reset tenants. ``create_app`` now accepts
  injectable state/queue backends, and the demo API persists settings so the
  scheduled dispatcher can discover enabled tenants. All of this is verified
  offline with fake clients; nothing here claims a live deployment.
- The live maintenance executor landed: `LiveWorkflow` runs the same agent
  boundary and deterministic planning as the sample, but reconciles against
  the provider travel calendar with conditional ETag writes. It only owns
  events whose private markers match, detects user edits by comparing the
  stored applied hash with the block content (padding included), treats a
  user-deleted block as a skip instead of recreating it, never touches past
  or already-started blocks, and deduplicates decisions by occurrence plus
  source revision so unchanged polls do not reopen resolved choices.
  `GoogleCalendarAdapter.list_blocks` reads only marked events and persists
  journey key, applied hash, source revision, policy revision, and padding in
  private extended properties. `LiveRunProcessor` completes the pipeline:
  settings -> source re-read -> place resolution -> agent -> executor ->
  atomic persist, with provider adapters injected for offline tests. A
  per-user Secrets Manager credential store and sample/live job dispatch
  complete the worker skeleton; live credential loading remains unverified.
- Sample sessions are now durable across Lambda instances. A
  ``SampleSnapshot`` (day, source events, skipped journeys, generation) is
  persisted through the same state-store contract in both SQLite and
  DynamoDB (24-hour TTL on the Dynamo item), and a cold worker rebuilds the
  tenant, including its previously applied blocks, before processing a job.
  The day/activity/run-decision reads now come from the state store, and
  decision ids are keyed by occurrence plus source revision with a shared
  stale-closing policy: an identical poll keeps one open decision, while a
  resolved conflict supersedes the older record instead of stacking stale
  alerts (previously the sample could leave old conflicts open forever).
- The documentation package landed: `docs/architecture.md` with an editable
  SVG and PNG export generated by `scripts/export_architecture.py`, privacy
  and third-party notices, an evidence-only evaluation log, and the
  `submission/` artifacts (story, fields, judge instructions, demo script,
  release checklist, optional Builder post). Every unshipped item is marked
  as such rather than asserted.
- Playwright (Chromium) now drives the judge path end to end and captures the
  four 3:2 gallery screenshots. The journey caught an inverted display-zone
  offset in `frontend/src/time.ts` (edited appointments shifted by twice the
  zone offset) and led to a one-retry guard for transient proxy keep-alive
  failures in the API client. A GitHub Actions workflow runs the backend,
  frontend, and e2e checks, but it has not executed yet because the repository
  is not pushed.
- Uncertain meetings are no longer silently ignored. An opaque event with a
  location but unknown mode (both venue and online marker, per the Google
  mapping) now produces a `hybrid_meeting` decision in the deterministic
  planner, in the Strands journey pairs, and in the live executor, instead of
  being dropped between physical journeys; an online/no-location event still
  occupies time without creating travel. Ten-run stability and latency
  evidence are recorded in `docs/evaluation.md`.
- Managed blocks now carry and persist the plan's private-property set:
  `glideUser`, `glideOriginOccurrence`, and `glideDestinationOccurrence` join
  the journey key, applied hash, source revision, policy revision, padding,
  and schema version on the Google event. The live executor also re-reads the
  source calendar immediately before its first mutation and raises
  `StaleSourceError` when the fingerprint changed, so the queue retries with
  fresh plans instead of applying a stale reconciliation.
- The timeline shows each travel block's resolved origin and destination
  names, arrival buffer, and update status (from the latest receipt) instead
  of a bare "Reserved driving time" row; blocks persist their
  `origin_occurrence_id` so the labels survive reconciliation. The sample
  settings panel gained a start-address control, and `PATCH /api/settings`
  accepts an explicit null to clear it. These behaviors are asserted by the
  Playwright journey.
- Decision answers now trigger their own bounded run. Resolving a skip
  returns the queued run id, and the worker replans against current events
  through the same FIFO path as manual and scheduled checks, so the UI shows
  the reconciled result instead of leaving the decision and calendar
  inconsistent until the next manual recheck. Covered by an API test and a
  Playwright journey.
- Opaque all-day events are no longer treated as ordinary meetings: the
  Google mapping marks them (`all_day`), and the deterministic planner,
  Strands journey pairs, and live executor surface one day-level
  `all_day` decision instead of treating the day as freely schedulable.
  The time-arithmetic verification matrix also gained explicit tests for
  exact-boundary fitting, past departures, opaque virtual meetings forcing
  earlier blocks, midnight-crossing busy intervals, and the London DST
  spring-forward normalization.
- The dispatcher now walks settings in bounded scan pages (default 100) so an
  invocation's work stays capped; an active/due GSI remains a follow-up. The
  FIFO visibility timeout was raised above the worker timeout (300s vs 150s)
  per the background-operation rules. Normalization coverage gained tests for
  Google pagination through `list_next`, moved recurring instances keeping a
  distinct occurrence identity, and location-less events classifying as
  unknown rather than physical.
- An unresolved journey now suspends the downstream chain instead of silently
  assuming the person completed an impossible leg. When a journey produces an
  `unknown_location` or `insufficient_time` decision, every following journey
  becomes an explicit `downstream_uncertain` decision in the deterministic
  planner, the Strands proposal validation (which requires the upstream to be
  genuinely unresolved), and the live executor, until a corrected location or
  skip restores a coherent itinerary.
- The live disconnect lifecycle landed: pause first (so the dispatcher stops
  creating jobs), best-effort removal of owned travel blocks, then credential
  revocation and secret deletion, with each failed step surfaced as an
  explicit warning (including the manual "Glide Travel" calendar removal
  fallback). A prompt-injection fixture also proves malicious event titles
  flow through planning as inert data.
- The recovery matrix gained explicit tests: a worker that crashes after a
  provider write reconciles on the next run without duplicates, reprocessing
  the same run is harmless (one create receipt), cross-tenant run access
  returns 404, and a padding change updates the block on recheck.
- A local scheduled-run dispatcher mirrors the deployed EventBridge rule:
  every `GLIDE_SCHEDULE_INTERVAL` it enqueues one check per watching sample
  tenant that is due, through the same queue path, so source edits reconcile
  with the browser closed. (The 12 September change made the local tick 20s
  and the deployed tick 300s; the 15-minute tenant interval governs the
  actual cadence on both.) A test proves an appointment edit updates both
  blocks without any recheck request. The landing page also now states the
  MVP reads only the primary calendar and never edits source appointments.
- The denied-scope check is now a pure, tested function
  (`ensure_required_scopes`) instead of inline code, and
  `scripts/clean_setup_trial.ps1` reproduces the release gates from a clean
  copy: frozen `uv sync`, the full test suite, ruff, `npm ci`, typecheck, and
  build all pass (122 tests).
- The repository is now initialized with an initial commit (127 files) after
  a staged-path audit confirmed `.env`, `private.md`, databases, build
  artifacts, caches, and node/venv directories are ignored. A public remote
  and push remain owner actions.

## 9 September

- One API surface serves both identities through a `Principal` dependency: a
  sample tenant is pinned by `X-Glide-Session` and never touches live
  credentials, while an encrypted Google session cookie identifies a live
  user whose settings, events, runs, decisions, and activity are scoped by
  owner checks. Second-user isolation is test-enforced.
- OAuth transactions carry the PKCE verifier plus an expiry and are bound to
  the initiating browser in an HttpOnly, path-scoped cookie consumed once on
  callback; refresh tokens are persisted per user and written back after a
  refresh, so the grant survives access-token expiry.
- Conditional Google writes put `If-Match` on the real request headers (the
  installed SDK's `execute` accepts no headers argument), and event ids are
  derived from journey plus source revision. Because Google reserves the ids
  of deleted events, the revision scope is what defines the reopen rule: a
  manual deletion stays deleted until the source revision changes.
- Manual edits are preserved on every removal path, and resolved skips are
  durable in decisions keyed to the source revision rather than relying on
  open-decision records alone.
- Deployed processing commits a queued run row before enqueueing scheduled
  jobs, restores durable state on every access, enforces snapshot expiry,
  fences results against settings-revision changes, and gates production
  imports behind `GLIDE_ENV=production` so Lambda never initializes SQLite.
- Bedrock configuration fails loudly in production instead of silently
  falling back to deterministic planning; the deterministic fallback remains
  a labeled local-development mode only.

## 10 September

- Owner requirement override: app-owned travel blocks are written directly
  into the user's primary calendar with event-level write consent, replacing
  the earlier separate Glide Travel calendar design in this plan. Ordinary
  appointments are preserved; managed events are identified by private
  extension properties, excluded from source planning, and never trigger
  calendar deletion during cleanup.
- OAuth now requests `openid`, `email`, and `calendar.events.owned`. The old
  `calendar.app.created` scope cannot write to the primary calendar, and the
  application enforces the Glide-only restriction itself.
- Existing connections are reconciled rather than migrated destructively:
  legacy grants without the new scope are flagged for reconnect, and no old
  data is deleted blindly.
- Amazon Location `SearchText` always sends a geographic selector
  (`BiasPosition`), satisfying the API's exactly-one-of requirement; the live
  call returned real places. Live `CalculateRoutes` returned a 513-second
  driving estimate, and Bedrock `eu.amazon.nova-2-lite-v1:0` answered a real
  call in `eu-west-1`.
- The Lambda functions run on `x86_64` and are packaged by
  `scripts/build_lambda.ps1` using uv with a Linux/Python 3.12 target, because
  SAM's host-side pip builder cannot evaluate Windows-only markers correctly
  on this machine. The template's circular CloudFront/API dependency was
  removed by parameterizing `FrontendOrigin`; deployment therefore happens in
  two passes (placeholder origin, then the real distribution origin).
- Real-account validation forced further template corrections: the SAM policy
  template is `AWSSecretsManagerGetSecretValuePolicy`; CloudFront rejects
  header/cookie cache keys when caching is disabled, so `/api/*` uses the
  managed `CachingDisabled` policy (headers/cookies/query still reach the
  origin via `AllViewerExceptHostHeader`); Lambda `LoggingConfig.LogGroup`
  takes the log group name, not an ARN; `ReservedConcurrentExecutions` was
  removed because a new account cannot drop its unreserved minimum; the worker
  needs both `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`
  scoped to the tested foundation model and inference profiles; and Mangum
  must strip the API Gateway stage via `api_gateway_base_path`.
- The stack `glide` is live in `eu-west-1` at
  `https://d3tvxy281s2u11.cloudfront.net`; one deployed sample check has
  completed through SQS -> worker -> DynamoDB -> Bedrock. Deployed runs
  occasionally stop at the agent turn budget with `AgentProposalMissing`,
  which is being hardened before the ten live maintenance sequences.

## 11 September

- AWS MCP access moved from four legacy awslabs Docker servers
  (`cloudwatch-logs`, `dynamodb`, `sqs`, `lambda`, digest-pinned images) to the
  managed **AWS MCP Server** (Agent Toolkit for AWS) reached through the
  official SigV4 proxy `mcp-proxy-for-aws-cli`. Upstream now presents the
  Agent Toolkit as the successor to the awslabs MCP servers and asks clients to
  remove the older AWS servers so overlapping tools do not confuse the agent;
  the Docker transport also made AWS reads depend on Docker Desktop running,
  which it was not.
- The endpoint is `https://aws-mcp.eu-central-1.api.aws/mcp`: only
  `eu-central-1` and `us-east-1` exist (verified by DNS/HTTP probe and the
  official user guide). The endpoint region is the SigV4 signing region, so the
  config must not pass `--region`; the profile's region (`eu-west-1`) becomes
  the session's default working region, so Glide resources are addressed
  without repeating the region.
- Verified end to end on 11 September with the owner's refreshed `glide`
  session: a single `aws___run_script` call through the proxy executed seven
  read-only API calls (CloudFormation `DescribeStacks`, Lambda `ListFunctions`
  plus `GetFunctionConcurrency` for all three functions, and SQS
  `GetQueueAttributes` for both queues) and returned the stack status and queue
  depths. The server exposes `aws___run_script`, `aws___get_presigned_url`,
  `aws___get_tasks`, the read-only serverless diagnostics capability, and the
  AWS knowledge tools.
- Codex-side check: a fresh non-interactive session (`codex exec -s read-only`)
  loaded `.codex/config.toml`, started `aws-mcp` through the pinned proxy, and
  completed a real `aws___list_regions` call (37 regions returned). The MCP
  server is therefore reachable from Codex itself, not only from a manual
  proxy run.
- Caveat recorded: the proxy's `--read-only` flag hides every non-read-only
  tool, including `aws___run_script`, so it removes all API access and is not a
  usable guard here. The per-call guard is
  `default_tools_approval_mode = "writes"` (Codex prompts when a tool is not
  marked read-only); the account-side guard is IAM scoping plus the
  `aws:CalledViaAWSMCP` / `aws:ViaAWSMCPService` condition keys.
- Deploys still run through `scripts/deploy.ps1` and SAM; the MCP reads and
  diagnoses the account rather than replacing the deploy path.
- Observed while verifying (not produced by this change): the `glide` stack hit
  `UPDATE_ROLLBACK_FAILED` after an `HttpApiStage` update failure at 19:15 UTC
  on 11 September, and was terminal in `UPDATE_ROLLBACK_COMPLETE` when
  re-checked. All three functions reported no reserved concurrency, the job
  queue was empty, and the DLQ held 76 messages with `glide-dlq-depth` in
  ALARM.
- The live `.codex/config.toml` still differs from the staged template for
  `google-calendar` (service-account env var pointing at a `credentials.json`
  that does not exist; the repository has `secrets/google-oauth-client.json`)
  and `playwright` (approval mode `auto`). The AWS section was applied to the
  live file surgically for that reason; running `scripts/install-mcps.ps1`
  would reset those two entries to the template.

## 11 September

- **Decision: announce a decision by email now, Slack next.** The hackathon
  theme is that the agent "runs quietly in the background and only pings you
  when there's a real decision to make", and the 11 September review found no
  notification path at all. Amazon SES was chosen as the first transport
  because it needs no phone-number registration, costs $0.10 per thousand
  messages, and the only recipients in the judged demo are the owner's own
  test accounts, which a sandbox account can already reach.
- **Alternatives considered.** SNS SMS is a stronger on-camera moment (a real
  text) but starts in the SMS sandbox with verified destinations and a $1
  monthly cap, and leaving the sandbox needs a support case describing opt-in
  and templates. A Slack bot is the better long-term channel for professional
  users, but it needs per-user OAuth and a workspace, so it is deliberately
  deferred.
- **The seam is transport-agnostic.** `DecisionNotifier` (adapters/interfaces)
  has one method; `deliver_open_decisions` owns the once-only rule and stamps
  `Decision.notified_at`; `carry_notification_state` re-applies the stamp to
  the fresh decision objects each run rebuilds from the calendar. Adding a
  Slack adapter later means one new class and no change to the workflow.
- **Consequences.** Notification is opt-in contact data, defaults to the
  Google sign-in address, can be paused or cleared in Settings, and is
  rejected for anonymous sample sessions so the public demo cannot be used to
  send mail. A transport failure logs the decision id and error type (never
  the address) and leaves the decision unmarked, so the next scheduled check
  retries it; a crash between commit and send can therefore duplicate at most
  one message, which is the safe direction to fail.

## 12 September

- **Decision: managed travel blocks are green, and stay in the primary
  calendar.** The request was to either give Glide its own calendar coloured
  green or to colour the events green. Event colour wins on the record already
  set on 10 September: a second calendar needs `calendar.app.created` (or
  broader) consent to create and list it, which would force every existing
  grant through a reconnect, while `colorId` on an event the app already owns
  needs no new scope at all.
- **Implementation.** `GoogleCalendarAdapter` sends Google's "Basil" green
  (event palette id `10`) in every block body, so creates and content updates
  both apply it. Blocks written before this change keep their current colour
  until their next content update re-applies the body; there is no
  write-on-read backfill, because `list_blocks` stays read-only.
- **Consequences.** A colour-only manual edit to a Glide block is not treated
  as a manual override, so the next reconciliation restores green. That is the
  same rule as before for every other presentation field Glide owns.
- **Decision: background watching is the visible default for the sample and an
  explicit opt-in for a live calendar.** The theme is an agent that runs
  without being opened, but the first build required a judge (or owner) to
  press Recheck now and hid the scheduled work behind that button. A sample
  session now watches its fictional day from creation; connecting Google sets
  the account up paused, and one "Start watching" control puts it in the
  background and runs the first check immediately.
- **Cost ceiling.** `UserSettings.background_check` and
  `background_interval_minutes` are the consent and the ceiling. The
  dispatcher schedules a tenant only while it is watching *and* due, using a
  small per-tenant schedule pointer (`SCHEDULE#STATE`) rather than replaying
  run history. Samples have a 15-minute floor and at most three new sessions
  are enqueued per tick; expired snapshots are skipped. Sample runs stay on
  the deterministic processor, so this bounds queue and DynamoDB writes, not
  model spend. A paused one-off check never turns into recurring work.
- **Consequences.** The API exposes `automation` on the day response (watching,
  interval, last/next check, checks since last view) derived from the same
  durable pointer, and the web client polls while visible and refetches on
  focus, so a decision raised in the background appears without a click.
  `last_viewed_at` is written at most every ten minutes because the day route
  is polled; it never bumps the settings revision and so never fences an
  in-flight run.
