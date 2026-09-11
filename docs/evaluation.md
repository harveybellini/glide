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

## Planned, not yet measured

- Ten consecutive canonical integrated runs and how many used live providers.
- Sample-run and background-run latency targets (60 seconds and one polling
  interval plus processing) against the deployed stack, plus a
  browser-closed scheduled run.
- Two unfamiliar testers resolving a conflict without verbal help.
- Manual-vs-Glide task comparison (method and sample size required before any
  time-saving claim).
- Provider call counts, duplicates across retries, and estimated cost on a
  real account.

## Evaluation criteria mapping

- Technical implementation: Strands tools/providers, background jobs, replay-
  safe reconciliation, durable state (implemented; Bedrock/Location and the
  deployed sample pipeline have live evidence; the live Google path is
  connected but has not yet produced an accepted proposal, so no real
  calendar write is recorded).
- Design: onboarding → maintained calendar → understandable decisions.
- Potential impact: the canonical multi-stop day, honest conflict shortfall.
- Presentation: real calendar changes in the video plus a reproducible
  sample path for judges.
