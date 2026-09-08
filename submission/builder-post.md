# Optional AWS Builder article

> Publish only after the live deployment exists. One strong post is the
> default; this is a draft, not a claim.

**Title:** A calendar agent that reserves the time to get there — built with
Strands Agents for Humans

**Hook:** The gap between two appointments is not free if it is the drive
between them. Glide reads a calendar, estimates the route, and writes the
travel block into its own calendar — then keeps that layer in sync when
appointments move.

**Body outline:**

1. The problem: travel time is invisible to most calendars.
2. The boundary that made it safe: an agent proposes with six typed tools;
   deterministic code validates references, owns arithmetic, and writes only
   to the app-created calendar with conditional ETag requests.
3. The stack: Strands Agents SDK on Amazon Bedrock, Amazon Location
   Places/Routes V2, SQS FIFO + EventBridge, DynamoDB single-table state,
   CloudFront + API Gateway + Lambda.
4. The reconciliation story: no duplicates on rerun, respect for manual
   edits, decisions instead of silent guesses.
5. What we measured and what we would build next.

**Tags:** Agents for Humans, Amazon Bedrock, Strands Agents, Amazon Location,
Google Calendar, serverless.
