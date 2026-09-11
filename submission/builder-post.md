# Optional AWS Builder article

> The live deployment exists at https://d3tvxy281s2u11.cloudfront.net; publish after the remaining Google consent and submission checks. One strong post is the
> default; this is a draft, not a claim.

**Title:** A calendar agent that reserves the time to get there — built with
Strands Agents for Humans

**Hook:** The gap between two appointments is not free if it is the drive
between them. Glide reads a calendar, estimates the route, and writes a marked
travel block into the primary calendar — then keeps that layer in sync when
appointments move.

**Body outline:**

1. The problem: travel time is invisible to most calendars.
2. The boundary that made it safe: an agent proposes with six typed tools;
   deterministic code validates references, owns arithmetic, and writes only
   its own marked travel blocks into the primary calendar with conditional
   ETag requests.
3. The stack: Strands Agents SDK on Amazon Bedrock, Amazon Location
   Places/Routes V2, SQS FIFO + EventBridge, DynamoDB single-table state,
   Amazon SES for the decision email, CloudFront + API Gateway + Lambda.
4. The reconciliation story: no duplicates on rerun, respect for manual
   edits, decisions instead of silent guesses.
5. Staying quiet by construction: the worker emails only when a decision
   opens, the once-only mark is persisted with the decision, and a failed
   send is retried by the next scheduled check instead of being marked sent.
6. What we measured and what we would build next: with more time, the same
   decision card as a Slack direct message with inline actions, so the user
   never has to open the app to answer.

**Tags:** Agents for Humans, Amazon Bedrock, Strands Agents, Amazon Location,
Google Calendar, serverless.
