# Evaluation

This records measured evidence only. Unmeasured targets are listed and are
not described as results.

## Automated checks (measured, 2026-09-14)

- `uv run pytest -q`: **393 passed**, 0 failures.
- `uv run ruff check .`: clean.
- Frontend `npm run typecheck` and `npm run build`: pass.
- Playwright `npm run e2e`: **24 passed** (design, judge path, guided tour,
  version monitor, API retry, and screenshot captures).
- `uv run python scripts/validate_template.py`: OK.
- `sam validate --lint -t infra/template.yaml`: the template is valid.
- `uv run python scripts/version.py check`: 0.4.2 in sync across the seven
  declarations; `docs/openapi.json` regenerated with the `/api/watching`
  routes.
- `scripts/run_sample.py`: canonical first check (one block, one 10-minute
  shortfall), move, repeat with `unchanged` receipts, delete, and direct
  A-to-C reconciliation.
- All ten captures in `submission/screenshots/` were retaken from the deployed
  0.4.2 release. The four gallery shots stay 3:2 at 1200 x 800, and every
  capture shows the sample label and the footer build badge.

## Deployed background watching (measured, 2026-09-14)

Deployed stack `glide` in `eu-west-1`, build 0.4.2, cross-checked from the
workspace:

- `/version.json` and `/api/health` both report `0.4.2` with `dirty: false`,
  and the live version-monitor browser check passes against both.
- `scripts/verify_deployed_sample.py` passed end to end: a fresh sample
  session is created watching, the first check returns `needs_input` with one
  block and one open decision, moving the middle appointment reaches
  `completed` with two blocks and no open decisions, the repeat returns all
  `unchanged` receipts, and a `trigger=schedule` run then arrives with no
  browser open. The recorded run saw the scheduled check 123 seconds after
  session creation; an independent rerun measured 56 seconds.
- Both travel blocks survived the scheduled run. The hosted sample stays
  provider-free, so it cannot generate model spend.

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
  `eu-west-1`.
- `https://d3tvxy281s2u11.cloudfront.net/` returns 200 and `/api/health`
  returns ok.
- `POST /api/demo/session` returns 201 through CloudFront.
- One sample check completed through SQS -> worker -> DynamoDB -> real
  Bedrock: status `needs_input`, one travel block, one decision.

## Live Google proof (measured, 2026-09-11)

The owner's dedicated Google test account (primary calendar), driven through
the deployed stack in `eu-west-1`. Fictional appointments were seeded for
12 September (Big Ben 09:30, The Shard 12:15, Canary Wharf 15:30, London) and
deleted again after the run.

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
  (manually edited travel events are kept); settings flipped to paused,
  `/api/auth/status` reported `connected: false` with the provider still
  available, and the grant was revoked.
- Alarms at close: `glide-api-5xx`, `glide-api-throttles`, `glide-dlq-depth`,
  and `glide-worker-errors` all `OK`; job queue and dead-letter queue both 0
  visible / 0 in flight.

## Decision email (measured, 2026-09-12)

- Amazon SES in `eu-west-1`: the `slyx.uk` domain identity is verified and
  sending is enabled. The account is still in the sandbox
  (`ProductionAccessEnabled: false`), so only verified recipients can receive
  mail.
- The connected Google tenant has `notify_on_decisions: true` and a
  notification address on the verified domain.
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
  the day view renders it.
- Dispatcher unit tests prove: a tenant with `background_check` false or
  `enabled` false is never enqueued; the interval floor rejects a check
  before 15 minutes and accepts one at 15; at most three new sample sessions
  are enqueued per tick and the oldest-first rotation reaches all five
  sessions in the test; a snapshot past its `expires_at` is skipped; and a
  second invocation resumes from the durable scan cursor.
- `last_viewed_at` is written at most once per ten minutes by the day route
  and does not bump the settings revision, so polling cannot fence an
  in-flight run.
- Measured on the deployed stack on 14 September: the hosted sample's first
  scheduled run arrives with no browser action (123 s in the recorded run,
  56 s in an independent rerun) and the day response's `automation` payload
  drives the strip in the day view.

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
- Design: onboarding -> maintained calendar -> understandable decisions.
- Potential impact: the canonical multi-stop day, honest conflict shortfall.
- Presentation: real calendar changes in the video plus a reproducible
  sample path for judges.
