# Live proof runbook

Each step records evidence without secret-bearing screenshots; update
`docs/evaluation.md` and `submission/release-checklist.md` from the results.

## 0. Account configuration (owner)

1. **AWS**: complete. The `glide` profile authenticates, `eu-west-1` works for
   Amazon Location Places/Routes V2 and Bedrock
   `eu.amazon.nova-2-lite-v1:0` (access-tested), and the `glide` stack is
   deployed at `https://d3tvxy281s2u11.cloudfront.net`. Spending cap: USD 75.
   The profile's login session expires often; refresh it with
   `aws login --profile glide` before live CLI work such as tailing worker
   logs.
2. **Google Cloud**: enable Calendar API; OAuth consent screen in Testing
   mode with the owner's test account as a test user; a web OAuth client with
   `http://localhost:8000/api/auth/google/callback` and
   `https://d3tvxy281s2u11.cloudfront.net/api/auth/google/callback` as
   redirect URIs.
3. Copy `.env.example` to `.env` and fill `AWS_PROFILE`, `AWS_REGION`,
   `BEDROCK_MODEL_ID`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and a long
   random `GLIDE_SESSION_SECRET`.

## 1. Amazon Location (done)

Observed live on 10 September: Big Ben and The Shard resolved independently,
one real driving estimate (513 s, quality `live`), and one real Strands tool
loop that proposed `create feasible destination`. Repeat the script below only
if a new region/model is selected.

```powershell
$env:AWS_REGION = "<region>"
$env:BEDROCK_MODEL_ID = "<confirmed model id>"
# optional overrides; defaults are Big Ben and The Shard in London
$env:GLIDE_PLACE_QUERY_ORIGIN = "<origin venue>"
$env:GLIDE_PLACE_QUERY_DESTINATION = "<destination venue>"
uv run python scripts/live_smoke.py
```

Expected: two independently resolved places with coordinates, one real
driving duration between them, and one Strands tool loop over a synthetic
schedule whose place references and routes are real. Record provider,
region, and whether a no-route/throttle path was also observed.

## 2. Bedrock + Strands (done)

`eu.amazon.nova-2-lite-v1:0` answered a real `Converse` call (57 tokens) in
`eu-west-1`, and the live smoke completed a real Strands run. A deployed
sample check also completed through the worker with one block and one
decision.

## 3. Google Calendar (verified 11 September 2026)

The owner's browser consent is complete and the live path is proven: tenant
`google:<subject>` wrote two `Travel / Glide` blocks in 20.7 s,
ten consecutive runs finished in 10.3-15.5 s, repeats were idempotent, manual
edits and deletions were respected, a scheduled run finished with the browser
closed, and the disconnect revoked the grant. The steps below are the
re-runnable checklist; measurements live in `docs/evaluation.md`.

1. Start the API and frontend (`uv run uvicorn glide.api.app:app --reload`
   plus `npm run dev` in `frontend/`).
2. Connect via the landing page, then verify `/api/auth/session` reports the
   test account email.
3. Using a script or the API, list the primary calendar, resolve one place,
   and create/read/conditional-update/delete one `Travel · Glide` event in the
   primary calendar; reconnect and confirm managed events are re-adopted, and
   ordinary appointments are untouched.
4. Confirm refresh-token expiry behavior and reconnect (Testing-mode tokens
   can expire after seven days).

## 4. Integrated runs

Once Google, Location, and Bedrock all work, run ten consecutive checks and
record how many used each live provider, latency, duplicates, and receipts.
Capture the recording described in `submission/demo-script.md` only from
these real runs.

## 5. Decision email

1. Verify the sending identity in SES (see `docs/setup.md`, "Decision emails").
   Request production access only if someone other than a verified recipient
   must receive mail.
2. Deploy with `-NotificationFromEmail` and confirm the worker has
   `GLIDE_NOTIFICATION_FROM` set (`aws lambda get-function-configuration`).
3. Connect the Google test account, confirm the Settings panel shows the
   sign-in address, and force one shortfall.
4. Save the email headers, the message id, and a screenshot of the arriving
   message and the highlighted decision card it links to. Record the delivery
   latency from the run's `ended_at` to the received timestamp.
5. Re-run the check with the conflict unresolved and confirm no second email
   arrives (the `notified_at` mark), then clear the address in Settings and
   confirm a new decision sends nothing.

## Evidence to save

- Redacted screenshots/video per `submission/screenshots/README.md`.
- Measured numbers into `docs/evaluation.md`.
- The exact region and model id into the Devpost fields (never a guessed
  value).
