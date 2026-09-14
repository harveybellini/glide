# Evaluation

This records measured evidence only. Unmeasured targets are listed and are
not described as results.

## Automated checks (measured, 2026-09-08)

- `uv run pytest -q`: **122 passed** (scheduling arithmetic incl. boundary,
  past departure, virtual-meeting, midnight, and DST cases; normalization;
  reconciliation,
  Google mapping, Amazon Location mapping, auth, queue/persistence, live
  executor, durable sessions, Strands tool loop).
- `uv run ruff check .`: clean.
- Frontend `npm run typecheck` and `npm run build`: pass.

## Automated checks (measured, 2026-09-09)

- `uv run pytest -q`: **138 passed**. Added since 8 September: live-route
  wiring and tenant isolation, durable decision skips keyed to the source
  revision, settings-revision fencing, deterministic Google event ids,
  dispatcher expiry/paging bounds, and refresh-token persistence.
- `uv run ruff check .`: clean.
- Frontend `npm run typecheck` and `npm run build`: pass.
- Playwright `e2e/judge-path.spec.ts`: 4 passed against the local API and
  Vite dev server.
- `uv run python scripts/validate_template.py`: template invariants pass.
- Playwright end-to-end (`frontend/e2e/judge-path.spec.ts`): **2 passed** —
  the full judge path (create sample, conflict, inline edit, recheck resolves,
  repeat shows unchanged, reset) and a skip-link/labelled-controls smoke.
- Canonical sample script: first check produces one feasible block plus one
  10-minute shortfall; move → two blocks, no decisions; repeat → two
  unchanged noop receipts; delete → orphan removed and direct journey
  recomputed.
- Live local smoke: create → queued run → needs_input with one block and one
  open decision; move + recheck → completed with two blocks, zero open
  decisions, prior decision stale; reset clears blocks and decisions.
- Four 3:2 gallery screenshots captured from the local sample into
  `submission/screenshots/`.
- Ten consecutive canonical integrated runs (`scripts/run_ten_runs.py`):
  **10/10 passed**, mean 0.1 ms each, all fixture calendar/routes and the
  deterministic runner (no live providers). A queued API round trip measured
  ~1.0 s end to end, dominated by the default one-second worker poll
  interval, against the 60-second sample-run target.

The end-to-end journey caught and fixed a real defect before release: the
display-zone offset in `frontend/src/time.ts` was inverted, so appointments
edited in the UI shifted by twice the timezone offset; the journey also led to
a one-retry guard for transient proxy keep-alive failures in the API client.

## Automated checks (measured, 2026-09-10)

- `pytest`: **266 passed**. Added since 9 September: OAuth transaction
  persistence/binding/replay rejection, primary-calendar event writes with
  `calendar.events.owned`, Google deterministic event ids and 404/409/412
  mapping, DynamoDB pagination/batch retries and the IndexName fix, scheduler
  cursor persistence, settings/pause/disconnect fencing, production token
  revocation, cleanup warning surfacing, connected-user decision and
  location-correction controls, and the Amazon Location `SearchText`
  `BiasPosition` fix.
- `ruff check .`: clean.
- Frontend `tsc -b && vite build`: pass.

## Automated checks (measured, 2026-09-11)

- `uv run pytest`: **323 passed** (322 at the time of the deployed-state
  pass; a later test landed the same day). Added since 10 September: once-only
  decision notifications (policy, SES transport, settings guard, deep link),
  the live-tenant routes, and the deployed-state regression coverage.
- `uv run ruff check .`: clean.
- Frontend `tsc -b && vite build`: pass (36 modules).
- `uv run python scripts/validate_template.py`: OK.
- Deployed sample path, observed in the account: three anonymous runs reached
  `needs_input`/`completed` with the expected counts in under a second each;
  deployed idempotent repeats held.
- Live Google path, observed in the account: tenant
  `google:<subject>` (enabled) has six runs; two failed with
  `AgentProposalMissing` and four were still queued at the time of the check.
  No blocks, decisions, or receipts exist for that tenant.
- SES: no verified identities; the account is still in the sandbox, so the
  notification path is deployed but cannot deliver.
- `sam validate --lint`: the template is reported valid (after the policy
  template name, Lambda `LogGroup` ARN, and circular-dependency fixes).
- `scripts/build_lambda.ps1`: 51 MB Linux/x86_64 Python 3.12 bundle with the
  handler modules present.
- Canonical sample script re-verified: create ->  conflict ->  move ->  update ->
  idempotent repeat ->  delete ->  direct-journey reconciliation.
- `scripts/run_ten_runs.py`: 10/10 passed, mean 0.1 ms, fixture providers.
- `docs/openapi.json` regenerated from the current routes.

## Automated checks (measured, 2026-09-12)

- `uv run pytest -q`: **381 passed**, including the sample and live coverage of
  the shortfall override: an accepted "Add it anyway" books the travel block
  ending as the destination appointment starts, is honored while the source
  revision it was accepted against is unchanged, and is reconsidered after a
  source edit.
- `uv run ruff check .`: clean.
- Frontend `npm run typecheck` and `npm run build`: pass.
- Playwright `e2e/judge-path.spec.ts`: **5 passed**, including the add-anyway
  path (optional note, block appears, decision stays answered across a later
  check).

## Live provider smoke (measured, 2026-09-10)

- Amazon Location Places `SearchText`: Big Ben and The Shard resolved with
  coordinates (London bias; two independent queries).
- Amazon Location Routes `CalculateRoutes`: 513 s driving duration,
  quality `live`, between the two resolved venues.
- Bedrock `eu.amazon.nova-2-lite-v1:0` (eu-west-1): one conversation returned
  a valid reply; usage 53 input / 4 output tokens.
- `scripts/live_smoke.py` completed a real Strands/Bedrock tool loop over the
  synthetic schedule and proposed `create feasible destination`.

## Deployment (measured, 2026-09-10)

- Stack `glide` reached `CREATE_COMPLETE` and subsequent `UPDATE_COMPLETE` in
  `eu-west-1`; the two-pass deploy set the real CloudFront origin.
- `https://d3tvxy281s2u11.cloudfront.net/` returns 200 and `/api/health`
  returns `{"status":"ok","mode":"sample","version":"0.1.0"}`.
- `POST /api/demo/session` returns 201 through CloudFront.
- One sample check completed through SQS -> worker -> DynamoDB -> real
  Bedrock: status `needs_input`, one travel block, one decision.
- Known remaining issue: some deployed runs fail with `AgentProposalMissing`
  when the agent's turn budget is reached; this is being hardened.

## Live Google proof (measured, 2026-09-11)

Tenant: the owner's dedicated Google test account (primary calendar), driven
through the deployed stack in `eu-west-1`. Fictional appointments were seeded
for 12 September (Big Ben 09:30, The Shard 12:15, Canary Wharf 15:30, London)
and deleted again after the run.

- First live maintenance run: `needs_input` in **20.7 s**, two `Travel / Glide`
  blocks written to the primary calendar, one `unknown_start` decision (no
  start address configured), three source appointments untouched.
- Idempotent repeat: both journeys returned `unchanged`/`noop` receipts, no
  duplicate provider events, no false manual-edit decisions.
- Manual edit: moving a Glide block raised a `manual_edit` decision
  (`keep_manual_edit` / `replace_with_plan` / `skip_journey`) and the edited
  block was not overwritten.
- Manual deletion: deleting a Glide block raised a `manually_deleted` decision
  (`recreate_journey` / `skip_journey`) and the block was not recreated.
- Pause: a run while paused returned `paused` with no writes; resume restored
  automation.
- Ten consecutive live sequences: all ten reached a terminal status, latency
  10.3-15.5 s (mean 11.3 s); receipts across the batch were `create: applied`
  x2, `noop: unchanged` x3, `update: applied` x4; no failures.
- Scheduled maintenance with the browser closed: the dispatcher reported
  `enqueued: 1`, and the `trigger=schedule` run reached terminal `needs_input`
  in DynamoDB.
- Disconnect: `POST /api/auth/logout` returned `disconnected` with one warning
  (manually edited travel events are kept); settings flipped to paused, a
  later calendar read failed with a Google `RefreshError`, and
  `/api/auth/status` reported `connected: false` with the provider still
  available.
- Alarms at close: `glide-api-5xx`, `glide-api-throttles`, `glide-dlq-depth`,
  and `glide-worker-errors` all `OK`; job queue and dead-letter queue both 0
  visible / 0 in flight.

## Agent loop hardening (measured, 2026-09-11)

Four defects made the deployed live loop fail with `AgentProposalMissing`:
the proposal schema advertised actions the validator always rejects, places
resolved through `lookup_place` were not acceptable to `estimate_journey`, the
model was required to decide a `start_place` pair with no configured start
address, and the repair pass continued a conversation Bedrock refuses after a
turn-cap stop. After the fixes the real Nova Lite loop converged on the first
pass in 5 tool calls and produced three accepted plans.

## Decision email (measured, 2026-09-12)

- Amazon SES in `eu-west-1`: the `slyx.uk` domain identity is verified and
  sending is enabled. The account is still in the sandbox
  (`ProductionAccessEnabled: false`), so only verified recipients can receive
  mail.
- Deployed worker `glide-WorkerFunction-d6nb6fzzYV3a` carries
  `GLIDE_NOTIFICATION_FROM=harvey@slyx.uk` and
  `GLIDE_PUBLIC_BASE_URL=https://d3tvxy281s2u11.cloudfront.net`.
- The connected Google tenant has `notify_on_decisions: true` and a
  notification address on the verified domain (settings revision 12).
- Open decisions carry durable `notified_at` stamps: 2026-09-12 at 13:37,
  14:18, and 15:25 UTC. SES reported `SentLast24Hours: 7`.
- The owner confirmed the message reaches the inbox. The message id, headers,
  inbox screenshot, and the deep link opening the highlighted card will be
  captured while recording the demo video.

## Background autonomy (local and unit evidence, 2026-09-12)

- `pytest` is green, `ruff check .` is clean, and the frontend typecheck,
  production build, and 24 Playwright specs all pass on the working tree.
- A sample session is created watching (`background_check: true`, interval
  15 minutes); `GET /api/day` returns an `automation` object and the strip in
  the day view renders it. A manual probe against the local API showed
  `watching: true`, `interval_minutes: 15`, and null last/next check before
  the first scheduled run.
- Dispatcher unit tests prove: a tenant with `background_check` false or
  `enabled` false is never enqueued; the interval floor rejects a check
  before 15 minutes and accepts one at 15; at most three new sample sessions
  are enqueued per tick and the oldest-first rotation reaches all five
  sessions in the test; a snapshot past its `expires_at` is skipped; and a
  second invocation resumes from the durable scan cursor.
- `last_viewed_at` is written at most once per ten minutes by the day route
  and does not bump the settings revision, so polling cannot fence an
  in-flight run.
- Not yet measured on the deployed stack: the hosted sample's first scheduled
  run, the hosted `automation` payload, and the live "Start watching" path.
  `scripts/verify_deployed_sample.py` now asserts the scheduled sample run
  instead of asserting it can never happen; it must be run after a deploy
  before any hosted claim is made.
- Still to re-check while recording: an unresolved repeat and a cleared
  notification address produce no further mail.

## Planned, not yet measured

- Two unfamiliar testers resolving a conflict without verbal help.
- Manual-vs-Glide task comparison (method and sample size required before any
  time-saving claim).
- Provider call counts, duplicates across retries, and estimated cost on a
  real account.

## Evaluation criteria mapping

- Technical implementation: Strands tools/providers, background jobs, replay-
  safe reconciliation, durable state (implemented; Bedrock, Amazon Location,
  Google primary-calendar writes, and the decision email all have live
  evidence; ten consecutive live runs completed in 10.3-15.5 s).
- Design: onboarding → maintained calendar → understandable decisions.
- Potential impact: the canonical multi-stop day, honest conflict shortfall.
- Presentation: real calendar changes in the video plus a reproducible
  sample path for judges.
