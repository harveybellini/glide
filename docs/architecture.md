# Architecture

Editable diagram: [architecture.svg](architecture.svg) · export:
[architecture.png](architecture.png). Regenerate both with
`uv run python scripts/export_architecture.py`.

## Flow

1. The browser loads the React build from a private S3 bucket through
   CloudFront. `/api/*` routes to API Gateway with caching disabled and
   cookies, query strings, and the `X-Glide-Session` header forwarded.
2. The API Lambda runs the same FastAPI application as local development
   through Mangum. It authenticates Google sessions, creates/edits sample
   sessions, persists their snapshots, reads the day from DynamoDB, and
   enqueues checks on a FIFO queue (one message group per user). It runs no
   in-process worker.
3. Every five minutes an EventBridge rule runs the dispatcher, which scans
   persisted settings and enqueues one check per enabled tenant.
4. The worker Lambda drains the queue one message at a time. For sample
   users it rebuilds the synthetic tenant from its durable snapshot; for live
   users it re-reads Google Calendar, resolves places, runs the Strands agent
   over Bedrock, and reconciles travel blocks.
5. The run processor is deterministic application code: it validates every
   reference, owns all arithmetic, and writes only to the app-created Glide
   Travel calendar with conditional ETag requests. Results commit to
   DynamoDB in one transaction.

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
| Infrastructure | AWS SAM (`infra/template.yaml`) |

## Verification status

The components above are implemented and exercised offline (138 tests on
2026-09-09, ruff, frontend typecheck/build, four Playwright judge-path
checks, template structural validation). **Nothing is deployed yet**, and no
live Google, Amazon Location, or Bedrock call has been made. `infra/README.md`
lists the exact remaining prerequisites and unverified steps.
