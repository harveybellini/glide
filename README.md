# Glide

Glide reads a person's calendar, calculates driving time between physical appointments,
and reserves that time in a separate **Glide Travel** calendar. The first checked-in
workflow is an isolated sample day with fictional events and deterministic routes.

The MVP reads only the primary source calendar and never edits source appointments.
Managed travel blocks are private, busy events in an app-created calendar. The live
Google, Amazon Location, and Bedrock paths are implemented and offline-tested but not
yet run against real accounts.

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

The checked-in workflow uses fictional events and deterministic route fixtures.
- A Strands/Bedrock agent runner with six typed planning tools is implemented
  and offline-tested (bounded turns, a 120-second deadline, one repair retry,
  deterministic reference/arithmetic validation); the sample enables it with
  `GLIDE_AGENT_MODE=bedrock`.
- The live maintenance executor reconciles against a provider calendar with
  conditional ETag writes, manual-edit/deletion respect, and replay-safe
  idempotency.
- A SAM deployment skeleton (CloudFront/S3, API/worker/dispatcher Lambdas,
  SQS FIFO, DynamoDB) exists and is structurally validated, but nothing has
  been deployed.

Live Google Calendar, Amazon Location, and Bedrock calls remain the next
account-dependent milestones and are not yet claimed as working.

## More

- [Architecture](docs/architecture.md) (with [diagram](docs/architecture.png))
- [Setup and account configuration](docs/setup.md)
- [Decisions](docs/decisions.md) · [Privacy](docs/privacy.md)
- [Third-party notices](docs/third-party-notices.md)
- [Evaluation](docs/evaluation.md)
- [Submission artifacts](submission/)

MIT licensed. See [LICENSE](LICENSE).
