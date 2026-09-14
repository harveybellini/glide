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
Amazon SES email per decision, linking straight to the highlighted card. The
card also takes **Add it anyway** - with an optional note - when the person
would rather accept the shorter arrival buffer than skip the journey.
Google sign-in is wired to the live workflow end to end: the OAuth callback stores
the user's tokens, the API serves each signed-in user's own settings, events, runs,
and decisions, and a signed-out user can still run the synthetic sample day.
Amazon Location, Bedrock, Google Calendar, and SES have been exercised against
real accounts end to end. The deployed agent writes its marked travel blocks
to the test account's primary calendar; when a journey cannot fit it sends one
decision email linking back to the highlighted card. The hosted sample path
stays anonymous, deterministic, and provider-free.

Live demo: https://d3tvxy281s2u11.cloudfront.net (hosted sample day; no Google
account required).

The first visit opens a guided tour: it spotlights the control to press and
walks from the sample day through the first check, the travel blocks, a
decision, and the activity log. Finish or skip it and the day is yours;
**Show me around** starts it again, and `?tour=1` forces it open.

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

## Versions and the changelog

The footer of every page is the version monitor. It names the version, commit,
and build time this tab is running, compares them with the deployed
`/version.json` and the API's `/api/health` version, and offers a reload when a
newer build is live.

One version drives all of it: the [`VERSION`](VERSION) file is kept in step with
`pyproject.toml`, `backend/glide/__init__.py`, `frontend/package.json`, and its
lockfile. [`CHANGELOG.md`](CHANGELOG.md) records what each version means, and
every push must add an entry to it:

```powershell
# Describe the change under '## [Unreleased]' in CHANGELOG.md, then release it:
uv run python scripts/version.py bump minor
uv run python scripts/version.py check

# Once per clone: block pushes that skip the changelog.
powershell -ExecutionPolicy Bypass -File scripts/install-git-hooks.ps1
```

CI runs the same check, and [AGENTS.md](AGENTS.md) states the rule for agents
working in this repository.

## Current status

See [docs/evaluation.md](docs/evaluation.md) for the measured live proof and
[submission/release-checklist.md](submission/release-checklist.md) for what
remains before the Devpost entry is submitted. The decision email is live:
the SES domain identity is verified, the worker sends from it, and delivered
decisions carry a durable `notified_at` stamp.

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
- Background watching is visible and bounded: a tenant is scheduled only
  while it is watching and due (15 minutes by default, a 15-minute floor and
  a three-per-tick cap for anonymous samples), the day view shows the last
  check, the next one, and how many ran while the tab was closed, and the
  page refreshes itself so a background decision surfaces without a click.
- Decision notifications are once-only: a decision is stamped when Amazon SES
  accepts the message, the stamp survives the fresh decision objects every run
  rebuilds, and a failed send is retried by the next scheduled check. The
  address defaults to the Google account at sign-in and can be paused or
  cleared in Settings. A Slack adapter behind the same seam is deliberate
  future work, not a claim.
- The AWS stack (CloudFront/S3, API/worker/dispatcher Lambdas, SQS FIFO,
  DynamoDB) is deployed in `eu-west-1` and live at
  https://d3tvxy281s2u11.cloudfront.net; Amazon Location, Bedrock, Google
  primary-calendar writes, and the decision email all have live evidence.

## More

- [Architecture](docs/architecture.md) (with [diagram](docs/architecture.png))
- [Setup and account configuration](docs/setup.md)
- [Decision notifications: live status and recording steps](docs/notifications-next-steps.md)
- [Decisions](docs/decisions.md) · [Privacy](docs/privacy.md)
- [Third-party notices](docs/third-party-notices.md)
- [Evaluation](docs/evaluation.md)
- [Submission artifacts](submission/)

MIT licensed. See [LICENSE](LICENSE).
