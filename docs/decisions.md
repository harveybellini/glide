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
  rather than exposing a broken provider path. Live exchange remains unverified.
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
  every `GLIDE_SCHEDULE_INTERVAL` (default 300s) it enqueues one check per
  enabled sample tenant through the same queue path, so source edits
  reconcile with the browser closed. A test proves an appointment edit
  updates both blocks without any recheck request. The landing page also now
  states the MVP reads only the primary calendar and never edits source
  appointments.
- The denied-scope check is now a pure, tested function
  (`ensure_required_scopes`) instead of inline code, and
  `scripts/clean_setup_trial.ps1` reproduces the release gates from a clean
  copy: frozen `uv sync`, the full test suite, ruff, `npm ci`, typecheck, and
  build all pass (122 tests).
