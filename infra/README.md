# Deployment skeleton

This directory is the AWS SAM skeleton for the deployed application. It is
**not yet deployed or validated against a live account**, and nothing here
should be presented as shipped infrastructure.

## What the stack defines

- Private S3 bucket + CloudFront (OAC) serving the React build, with `/api/*`
  routed to API Gateway with caching disabled and cookies/query/headers
  forwarded.
- `glide.deploy.api` Lambda running the same FastAPI application as local
  development, backed by the DynamoDB state adapter and the SQS FIFO queue,
  with no in-process worker.
- A single on-demand DynamoDB table (`pk`/`sk` plus the `user-index` GSI and
  TTL on receipts) matching [dynamodb.py](../backend/glide/adapters/dynamodb.py).
- A FIFO job queue with a dead-letter queue after three receives, a worker
  Lambda draining one message at a time, and an EventBridge five-minute
  dispatcher that enqueues one check per enabled tenant in bounded scan pages.
  The queue visibility timeout (300s) stays above the worker timeout (150s).
- KMS-encrypted queue traffic and a generated Secrets Manager value for the
  session-cookie cipher.

## Prerequisites (all account-dependent)

- SAM CLI and AWS CLI configured with temporary credentials.
- A region where the chosen Bedrock model and Amazon Location Places/Routes
  V2 both work; `eu-west-1` is a candidate, not a verified setup.
- A confirmed, access-tested `BEDROCK_MODEL_ID`; the template does not guess
  one.
- A Google OAuth web client whose redirect URI includes the deployed
  `/api/auth/google/callback` URL.
- An agreed spending cap before any billable test.

## Deploy (only after the prerequisites above)

```powershell
uv run python scripts/validate_template.py
scripts/deploy.ps1 `
  -StackName glide `
  -Stage prod `
  -Region eu-west-1 `
  -BedrockModelId "<confirmed model id>" `
  -GoogleClientId "<client id>" `
  -GoogleClientSecret "<client secret>"
```

The script exports a SAM-compatible `requirements.txt` from `uv.lock`,
builds the frontend, runs `sam build`/`sam deploy`, uploads `frontend/dist`,
and invalidates CloudFront. It has never been run against an account.

## What is verified vs. not

Verified offline:

- `scripts/validate_template.py` checks template syntax, required resources,
  the GSI/TTL/FIFO invariants, and that every handler module exists.
- The DynamoDB and SQS adapters are covered by fake-client tests
  (`tests/unit/test_dynamodb.py`, `tests/unit/test_sqs_queue.py`).

Not yet done (and not claimed):

- `sam validate`/`sam build`/a real deployment have not been run.
- Sample sessions now persist a durable snapshot (24-hour TTL) so a cold
  Lambda worker rebuilds a tenant before processing its queued job; the
  day/activity reads are served from DynamoDB rather than process memory.
- The live run processor (Google read/write, real routes, Bedrock via the
  Strands runner) is wired into the worker and covered by fake-adapter tests;
  the per-user Secrets Manager credential loading has not been exercised
  against a real account.
- The Bedrock IAM policy uses `Resource: "*"` and must be tightened to the
  confirmed model once access is tested.
- The dispatcher scans rather than querying an active/due index; a dedicated
  GSI is the follow-up before high tenant counts.
