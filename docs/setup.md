# Glide local setup and account configuration

This document covers the local workflow and the account configuration for the
live providers. Amazon Location, Amazon Bedrock, and the AWS deployment are
verified; Google primary-calendar writes still require the owner's browser
consent against the deployed OAuth callback.

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
   - `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback`
5. Put the client ID and secret in `.env`.

The server also uses:

- `GLIDE_SESSION_SECRET` for encrypting local session cookies; generate a
  long random value for local testing and use KMS-backed secrets in AWS.
- `GLIDE_SECURE_COOKIES=true` only behind HTTPS.
- `GLIDE_FRONTEND_ORIGIN=http://localhost:5173` for the callback redirect.

Requested scopes:

- `openid`
- `email`
- `https://www.googleapis.com/auth/calendar.events.owned`

Glide reads and writes the user's **primary calendar** using event-level
consent. App-owned travel blocks are ordinary private busy events titled
`Travel Â· Glide`, marked with private extension properties so they are
recognized and excluded from source planning. The application never deletes
the user's calendar, and disconnect cleanup removes only untouched future
Glide-owned blocks. Ordinary appointments are preserved. The scope covers
events the user owns, so the application enforces the Glide-only restriction
itself. Testing-mode refresh tokens can expire after seven days, so reconnect
behavior is part of the release rather than assuming one build-time connection
survives judging.

Once connected, the signed-in day view reads the primary calendar directly and
shows Glide-owned travel blocks as managed events, the place search resolves
real starting addresses, and every queued check runs through the same local
worker using real Google and AWS providers. Refresh tokens are stored per user
and written back after a refresh, so the grant survives access-token expiry
without another OAuth prompt.

## 4. AWS

1. Use an AWS account or profile with temporary credentials; do not paste keys
   into chat or source.
2. Confirm access to the selected Amazon Bedrock model before recording a model
   ID. `eu.amazon.nova-2-lite-v1:0` in `eu-west-1` is verified.
3. Confirm access to Amazon Location Service Places and Routes V2 in the chosen
   region; `eu-west-1` is verified.
4. Confirm a cash spending cap before billable tests (USD 75 for this project).
5. Set `AWS_PROFILE`, `AWS_REGION`, and `BEDROCK_MODEL_ID` in `.env`, plus
   `GLIDE_AGENT_MODE=bedrock` to enable the Strands runner for sample runs.

Amazon Location Places requests that persist results must use the supported
storage intended use and account for its pricing. Do not store raw route
payloads or geometry.

The AWS SAM stack lives in [`infra/`](../infra/README.md): the CloudFront/S3
site, API/worker/dispatcher Lambdas, the FIFO queue, and the DynamoDB table.
`sam validate --lint` passes, and `scripts/build_lambda.ps1` produces the
Lambda bundle (uv resolves the dependencies for Python 3.12 on Linux x86_64).
Deploy with:

```powershell
scripts/deploy.ps1 -StackName glide -Stage prod -Region eu-west-1 `
  -BedrockModelId "eu.amazon.nova-2-lite-v1:0" `
  -GoogleClientId "<client id>" -GoogleClientSecret "<client secret>"
```

The script deploys twice: first with a placeholder frontend origin, then with
the real CloudFront origin once the distribution exists. Register the resulting
`https://<distribution>/api/auth/google/callback` URI in Google Cloud.

### Decision emails (Amazon SES, optional)

The worker sends at most one "needs your decision" email per decision when a
verified sending identity is configured. To enable it:

1. Verify a sending address or domain in SES **in the stack's region**
   (`eu-west-1`). New accounts are in the SES sandbox, which can only send to
   verified recipients — fine for the owner's test inbox, but request
   production access before emailing anyone else (AWS reviews with a 24-hour
   SLA). A verified domain with DKIM is the most deliverable option; do not
   use an `@gmail.com` From address, because Gmail publishes `p=reject` and
   the message will be rejected.
   Fastest verified path: verify the owner's own address and use it as both
   `-NotificationFromEmail` and the Google test account's address, so the one
   verification covers the sender and the recipient inside the sandbox.
2. Deploy with the identity:

```powershell
scripts/deploy.ps1 -StackName glide -Stage prod -Region eu-west-1 `
  -BedrockModelId "eu.amazon.nova-2-lite-v1:0" `
  -GoogleClientId "<client id>" -GoogleClientSecret "<client secret>" `
  -NotificationFromEmail "glide@example.com"
```

The stack creates the `AWS::SES::EmailIdentity` and grants `ses:SendEmail` on
that one identity. Leaving `NotificationFromEmail` empty (the default) deploys
everything else unchanged and disables sending. `GLIDE_PUBLIC_BASE_URL` comes
from the deployed CloudFront origin, so the email link lands on the live site.
Signed-in users can change or clear the address in Settings; anonymous sample
sessions cannot enable email.

When the AWS and Google accounts are ready, follow
[`docs/live-proof-runbook.md`](live-proof-runbook.md) to produce the live
provider evidence in order.

## 5. Live provider status

Verified on 10 September:

- AWS: the `glide` profile authenticates, Amazon Location Places/Routes work,
  and Bedrock `eu.amazon.nova-2-lite-v1:0` answers real calls in `eu-west-1`.
- Deployment: the stack is live at `https://d3tvxy281s2u11.cloudfront.net`
  and `/api/health` returns ok.

Re-verified on 11 September (deployed state):

- The redirect URI is registered and a Google test account is connected
  (`enabled: true`). Its runs have not yet produced an accepted proposal:
  two failed with `AgentProposalMissing` and four stayed queued, with no
  blocks, decisions, or receipts.
- Still needed: redeploy the agent-loop fix (turn budget 24 plus prompt
  rules, already in the working tree), get one live run to a terminal status,
  and then record the ten live maintenance sequences.
- Decision email: the stack accepts `-NotificationFromEmail`, but no SES
  identity is verified yet and the account is still in the SES sandbox. See
  "Decision emails" above and `notifications-next-steps.md`.
