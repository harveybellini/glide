# Live proof runbook

Run these steps only after the account configuration below is complete. Each
step records evidence without secret-bearing screenshots; update
`docs/evaluation.md` and `submission/release-checklist.md` from the results.

## 0. Account configuration (owner)

1. **AWS**: a profile with temporary credentials; a region where Amazon
   Location Places/Routes V2 and the chosen Bedrock model both work; a
   confirmed, access-tested model id; a cash spending cap before billable
   tests.
2. **Google Cloud**: enable Calendar API; OAuth consent screen in Testing
   mode with the owner's test account as a test user; a web OAuth client with
   `http://localhost:8000/api/auth/google/callback` (and the deployed
   callback later) as redirect URIs.
3. Copy `.env.example` to `.env` and fill `AWS_PROFILE`, `AWS_REGION`,
   `BEDROCK_MODEL_ID`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and a long
   random `GLIDE_SESSION_SECRET`.

## 1. Amazon Location

```powershell
$env:AWS_REGION = "<region>"
$env:BEDROCK_MODEL_ID = "<confirmed model id>"
uv run python scripts/live_smoke.py
```

Expected: one resolved place with coordinates and one driving duration in
seconds. Record provider, region, and whether a no-route/throttle path was
also observed.

## 2. Bedrock + Strands

The same script ends with one real Strands tool loop over the fictional
fixture calendar: it prints proposed actions (not blocks, since the executor
is not invoked against Google yet). Record model id, region, turns, and
latency.

## 3. Google Calendar

1. Start the API and frontend (`uv run uvicorn glide.api.app:app --reload`
   plus `npm run dev` in `frontend/`).
2. Connect via the landing page, then verify `/api/auth/session` reports the
   test account email.
3. Using a script or the API, list the primary calendar, resolve one place,
   and create/read/conditional-update/delete one `Travel · Glide` event; then
   connect again and confirm the same Glide Travel calendar is reused.
4. Confirm refresh-token expiry behavior and reconnect (Testing-mode tokens
   can expire after seven days).

## 4. Integrated runs

Once Google, Location, and Bedrock all work, run ten consecutive checks and
record how many used each live provider, latency, duplicates, and receipts.
Capture the recording described in `submission/demo-script.md` only from
these real runs.

## Evidence to save

- Redacted screenshots/video per `submission/screenshots/README.md`.
- Measured numbers into `docs/evaluation.md`.
- The exact region and model id into the Devpost fields (never a guessed
  value).
