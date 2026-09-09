# Glide local setup and account configuration

This document covers the foundation workflow and the account configuration
needed before live Google Calendar, Amazon Location, or Amazon Bedrock work.
The current repository runs an isolated sample day with fictional events and
deterministic routes; it does not yet prove live provider integration.

## 1. Local sample

Requirements:

- Python 3.12 or later
- `uv`

```powershell
uv sync --frozen
uv run python scripts/run_sample.py
uv run pytest
uv run ruff check .
```

The sample performs the canonical first check, moves the middle appointment,
repeats the check to prove idempotency, and deletes the appointment to show
reconciliation to a direct A-to-C journey.

## 1a. Local API and frontend

```powershell
uv run uvicorn glide.api.app:app --reload
```

In a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

`npm run typecheck` and `npm run build` are the production frontend checks.
The API's generated contract lives in `docs/openapi.json`; regenerate it after
changing routes with `uv run python scripts/export_openapi.py`.

End-to-end checks and gallery screenshots need the API and dev server running:

```powershell
# terminal 1
uv run uvicorn glide.api.app:app --reload
# terminal 2
cd frontend
npm run dev
# terminal 3 (frontend directory)
npm run e2e
npm run screenshots
```

The screenshots land in `submission/screenshots/` at 3:2. A GitHub Actions
workflow mirrors the backend, frontend, and e2e checks once the repository is
pushed.

Sample checks are queued and processed by an in-process worker. Completed
runs, plans, decisions, receipts, and travel blocks are persisted to a local
SQLite file (default `glide-local.db`, overridable with `GLIDE_LOCAL_DB`) so
run polling reads durable state rather than request memory. The worker poll
interval defaults to one second (`GLIDE_WORKER_POLL_INTERVAL`). Both files are
gitignored; the deployed system replaces the local queue with SQS and the file
with DynamoDB.

A local scheduler mirrors the deployed EventBridge rule: set
`GLIDE_SCHEDULE_INTERVAL` (seconds, default 300) and the running API will
recheck every enabled sample tenant on its own, so edits keep reconciling
even with the browser closed. Set it to `0` to disable background checks.

## 1b. Agent runner mode

Sample runs default to the deterministic offline planner. To run the real
Strands/Bedrock loop against the same synthetic calendar and routes, set:

```powershell
$env:GLIDE_AGENT_MODE = "bedrock"
$env:BEDROCK_MODEL_ID = "<confirmed model id>"
$env:AWS_REGION = "<region with access>"
```

Optional: `BEDROCK_MAX_TOKENS` caps each model response (default is the
provider default). When `BEDROCK_MODEL_ID` is missing, `GLIDE_AGENT_MODE` is
ignored and the deterministic runner is used with a logged warning. The
Strands runner enforces a ten-turn model budget and a 120-second deadline per
run; tool names, durations, safe reason codes, and usage are written to the
`glide.agent` logger. Offline tests stub the model and must not be presented
as a live agent demonstration.

## 2. Environment file

Copy `.env.example` to `.env` only if you are configuring live providers:

```powershell
Copy-Item .env.example .env
```

Never commit `.env`, OAuth client secrets, refresh tokens, credentials JSON, or
personal calendar recordings. The `.gitignore` already excludes these.

## 3. Google Calendar

Glide needs two OAuth client secrets and a designated test account.

1. Create or select a Google Cloud project.
2. Enable the **Google Calendar API**.
3. Configure the OAuth consent screen. In Testing mode, add the owner's
   designated Google account as a test user.
4. Create an OAuth web client and add these exact redirect URIs:
   - `http://localhost:8000/api/auth/google/callback`
   - the deployed callback URL once it exists
5. Put the client ID and secret in `.env`.

The server also uses:

- `GLIDE_SESSION_SECRET` for encrypting local session cookies; generate a
  long random value for local testing and use KMS-backed secrets in AWS.
- `GLIDE_SECURE_COOKIES=true` only behind HTTPS.
- `GLIDE_FRONTEND_ORIGIN=http://localhost:5173` for the callback redirect.

Requested scopes:

- `openid`
- `email`
- `https://www.googleapis.com/auth/calendar.events.readonly`
- `https://www.googleapis.com/auth/calendar.app.created`

The application reads only the selected primary calendar and writes only the
app-created **Glide Travel** calendar. Do not grant full calendar control.
Testing-mode refresh tokens can expire after seven days, so reconnect behavior
is part of the release rather than assuming one build-time connection survives
judging.

Once connected, the signed-in day view reads the primary calendar directly,
the place search resolves real starting addresses, and every queued check runs
through the same local worker using real Google and AWS providers. Refresh
tokens are stored per user and written back after a refresh, so the grant
survives access-token expiry without another OAuth prompt.

## 4. AWS

1. Use an AWS account or profile with temporary credentials; do not paste keys
   into chat or source.
2. Confirm access to the selected Amazon Bedrock model before recording a model
   ID.
3. Confirm access to Amazon Location Service Places and Routes V2 in the chosen
   region. `eu-west-1` is a candidate, not a verified setup.
4. Confirm a cash spending cap before billable tests.
5. Set `AWS_PROFILE`, `AWS_REGION`, and `BEDROCK_MODEL_ID` in `.env`, plus
   `GLIDE_AGENT_MODE=bedrock` to enable the Strands runner for sample runs.

Amazon Location Places requests that persist results must use the supported
storage intended use and account for its pricing. Do not store raw route
payloads or geometry.

The AWS SAM deployment skeleton lives in [`infra/`](../infra/README.md). It
defines the CloudFront/S3 site, API/worker/dispatcher Lambdas, the FIFO queue,
and the DynamoDB table, but it has not been deployed or validated with SAM
against a live account. Run `uv run python scripts/validate_template.py` for
the offline structural check; actual deployment waits for the prerequisites
in `infra/README.md`.

When the AWS and Google accounts are ready, follow
[`docs/live-proof-runbook.md`](live-proof-runbook.md) to produce the live
provider evidence in order.

## 5. Account requests to complete next

The first live proof is due on **9 September** and requires:

- Owner AWS access for Bedrock and Amazon Location.
- Google Cloud OAuth client with the local redirect URI.
- A designated Google test account with fictional appointments.
- An agreed spending cap.

Until those are available, the foundation, fixtures, scheduling arithmetic, and
sample reconciliation workflow can continue independently.
