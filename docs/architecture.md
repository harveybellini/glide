# Architecture

Editable diagram: [architecture.svg](architecture.svg) · export:
[architecture.png](architecture.png). Regenerate both with
`uv run python scripts/export_architecture.py`.

## Flow

1. The browser loads the React build from a private S3 bucket through
   CloudFront. `/api/*` routes to API Gateway with caching disabled and
   cookies, query strings, and the `X-Glide-Session` header forwarded.
2. The API Lambda runs the same FastAPI application as local development
   through Mangum. Google sign-in exchanges a PKCE/state-bound authorization
   code and stores each user's tokens in Secrets Manager (refreshed tokens
   are written back); the encrypted session cookie identifies a live user,
   while the `X-Glide-Session` header identifies an isolated sample tenant.
   The same routes serve both identities and the live day is read straight
   from Google Calendar. Checks are enqueued on a FIFO queue (one message
   group per user); the API runs no in-process worker.
3. Every five minutes an EventBridge rule runs the dispatcher, which scans
   persisted settings and enqueues a check for each tenant that is watching
   and due under its own interval (15 minutes by default, 15 minutes minimum
   for anonymous samples, and at most three new sample sessions per tick).
   The interval lives in a small per-tenant schedule pointer so the answer
   does not grow with the account's run history.
4. The worker Lambda drains the queue one message at a time. For sample
   users it rebuilds the synthetic tenant from its durable snapshot; for live
   users it re-reads Google Calendar, resolves places, runs the Strands agent
   over Bedrock, and reconciles travel blocks.
5. The run processor is deterministic application code: it validates every
   reference, owns all arithmetic, and writes Glide-owned travel blocks into
   the user's primary calendar with conditional ETag requests. Managed blocks
   are identified by private extension properties and excluded from source
   planning; ordinary appointments are never modified. Results commit to
   DynamoDB in one transaction.
6. After a committed result, the worker announces newly open decisions
   through the notification adapter. The deployed adapter sends one Amazon SES
   email per decision, stamped with `notified_at` so scheduled reruns stay
   silent; a send failure is logged and retried on the next check. The same
   seam is where a Slack adapter would go.
7. The web client polls the day every 30 seconds while its tab is visible, and
   refetches on focus, so a decision the agent raised in the background
   appears without the owner pressing anything. The day response carries an
   `automation` block (last and next check, checks since last view, watching
   state) derived from the same durable pointer.

## Components

| Component | Implementation |
| --- | --- |
| Web | React, TypeScript, Vite (`frontend/`) |
| API | FastAPI + Pydantic, wrapped by Mangum for Lambda (`backend/glide/api/`) |
| Agent | Strands Agents SDK over Amazon Bedrock, six typed tools, bounded turns and deadline (`backend/glide/agent/`) |
| Planning/reconciliation | Deterministic domain code (`backend/glide/domain/`) |
| Providers | Google Calendar V3, Amazon Location Places/Routes V2 (`backend/glide/adapters/`) |
| State | One DynamoDB table with a `user-index` GSI and receipt TTL; SQLite for local runs |
| Jobs | SQS FIFO + worker Lambda + EventBridge dispatcher; in-memory queue locally |
| Notifications | Amazon SES transactional email once per open decision; `notified_at` dedupe mark (`backend/glide/domain/notifications.py`) |
| Infrastructure | AWS SAM (`infra/template.yaml`) |

## Verification status

The components above are implemented and exercised offline (`pytest`, ruff,
frontend typecheck and production build, the Playwright judge-path and tour
checks, `sam validate --lint`). Real Amazon Location Places/Routes and real
Strands/Bedrock calls have been exercised against the live account, and the
AWS stack is deployed in `eu-west-1` at
`https://d3tvxy281s2u11.cloudfront.net` (`/api/health` returns ok; deployed
sample checks produce blocks and decisions through SQS, the worker, and
DynamoDB).

The live path is proven. On 11 September the owner's Google test account wrote
two `Travel / Glide` blocks in 20.7 s, ten consecutive live sequences finished
in 10.3-15.5 s, repeats were idempotent, and manual edits and deletions were
respected. On 12 September SES sent once-only decision mail from the verified
`slyx.uk` identity and the owner confirmed delivery (`docs/evaluation.md`).

Background watching is implemented and covered offline, and the 14 September
deploy shipped it (`0.4.1`, commit `f496f53`). The hosted sample's first
scheduled run and the live "Start watching" path have not been measured on the
deployed stack; run `scripts/verify_deployed_sample.py` after a deploy before
making a hosted claim.
