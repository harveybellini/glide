# Glide

Glide reads a person's calendar, calculates driving time between physical appointments,
and reserves that time directly in the user's primary calendar. The first checked-in
workflow is an isolated sample day with fictional events and deterministic routes.

The MVP reads the primary calendar and never edits source appointments. Managed
travel blocks are private, busy, green `Travel · Glide` events in that same
calendar, identified by private extension properties and excluded from source
planning; ordinary appointments are preserved, and the user's calendar is never
deleted.
When a journey cannot fit, Glide explains the shortfall and sends at most one
Amazon SES email per decision, linking straight to the highlighted card.
Google sign-in is wired to the live workflow end to end: the OAuth callback stores
the user's tokens, the API serves each signed-in user's own settings, events, runs,
and decisions, and a signed-out user can still run the synthetic sample day.
Amazon Location and Bedrock calls have been exercised against a real account,
and a Google account is connected end to end. Live planning currently fails
before any calendar write (`AgentProposalMissing`), so no live
primary-calendar proof is recorded yet; the deployed sample path is verified.

Live demo: https://d3tvxy281s2u11.cloudfront.net (hosted sample day; no Google
account required).

## Run the sample workflow

```powershell
uv sync --frozen
uv run python scripts/run_sample.py
uv run pytest
uv run ruff check .
```

The script runs the canonical first check, moves the middle appointment, reruns twice to
prove idempotency, then deletes the appointment and reconciles the obsolete blocks.

See [docs/setup.md](docs/setup.md) for local setup and the account configuration that will
be needed for live provider work.

## Run the local web sample

Start the API in one terminal:

```powershell
uv run uvicorn glide.api.app:app --reload
```

Start the React interface in another:

```powershell
cd frontend
npm ci
npm run dev
```

The frontend proxies `/api` to the local server. Production checks are
`npm run typecheck` and `npm run build`. The OpenAPI contract is generated
with `uv run python scripts/export_openapi.py` and committed to
[docs/openapi.json](docs/openapi.json).

## Current status

See the [9 September implementation review](docs/next-steps.md) and the
[progress log](docs/completion-progress.md) for the latest verification
results, including the 11 September deployed-state pass.

The checked-in sample workflow uses fictional events and deterministic route fixtures.
- A Strands/Bedrock agent runner with six typed planning tools is implemented
  and offline-tested (bounded turns, a 120-second deadline, one repair retry,
  deterministic reference/arithmetic validation); the sample enables it with
  `GLIDE_AGENT_MODE=bedrock`. In production the runner fails loudly instead of
  silently falling back to deterministic planning.
- The live maintenance executor reconciles against a provider calendar with
  conditional `If-Match` writes, manual-edit/deletion respect (including durable
  skips that reopen on source-revision change), deterministic event ids, and
  replay-safe idempotency.
- The API shares one surface for sample sessions and signed-in Google users,
  with per-user ownership checks, pause/resume, and real disconnect that revokes
  the grant and cleans up owned travel blocks.
- Deployed background processing reloads durable state on every job, fences
  results against settings changes, skips expired tenants, and persists queued
  run rows so scheduled results are never discarded.
- Decision notifications are once-only: a decision is stamped when Amazon SES
  accepts the message, the stamp survives the fresh decision objects every run
  rebuilds, and a failed send is retried by the next scheduled check. The
  address defaults to the Google account at sign-in and can be paused or
  cleared in Settings. A Slack adapter behind the same seam is deliberate
  future work, not a claim.
- The AWS stack (CloudFront/S3, API/worker/dispatcher Lambdas, SQS FIFO,
  DynamoDB) is deployed in `eu-west-1` and live at
  https://d3tvxy281s2u11.cloudfront.net; Amazon Location and Bedrock calls
  have real evidence, while Google primary-calendar writes await the owner's
  browser consent.

## More

- [Architecture](docs/architecture.md) (with [diagram](docs/architecture.png))
- [Setup and account configuration](docs/setup.md)
- [Decision notifications: steps to finish](docs/notifications-next-steps.md)
- [Decisions](docs/decisions.md) · [Privacy](docs/privacy.md)
- [Third-party notices](docs/third-party-notices.md)
- [Evaluation](docs/evaluation.md)
- [Submission artifacts](submission/)

MIT licensed. See [LICENSE](LICENSE).
