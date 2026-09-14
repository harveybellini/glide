# AWS Builder article

Publish-ready draft, 14 September 2026. Publishing is an owner action: it
needs a signed-in builder.aws.com session, so nothing here is public until the
owner posts it.

## Publishing notes (not for publication)

- The hackathon bonus requires the published title to contain **Agents for
  Humans**; the draft title does. Each public post is worth +0.2, up to three
  posts (+0.6 total). This is the one strong post; a second and third angle
  would need their own drafts.
- Alternate titles from the project plan if this one does not land:
  "Agents for Humans: Building Glide, a Calendar Agent That Makes Room for
  Travel", or "Agents for Humans: what a calendar agent should be allowed to
  decide". Both already satisfy the title requirement.
- Publish on builder.aws.com, then paste the public URL into
  `submission/fields.md` and tick the release-check item. Until then the
  article is a draft, not a claim of a published post.
- Publish after the entry video is public: the article points to it, and the
  rules require the video anyway.
- The images below link to the public repository and were verified reachable
  on 14 September 2026. The gallery was retaken from the deployed 0.4.2 build
  the same day, but those files are not committed or pushed yet, so the raw
  links still serve the earlier captures until the push lands. Confirm the
  three links render the recaptured images before publishing.
- Byline: the owner's AWS Builder profile name. No name is invented here.
- Facts and numbers below were re-checked against the working tree and the
  deployed stack on 14 September 2026 (`393 passed`, version `0.4.2`, commit
  `0676869`, `/api/health` and `/version.json` in agreement).

---

# Agents for Humans: a calendar agent that reserves the time to get there

**A day of appointments is also a day of driving. Glide reads the primary
calendar, prices the journey between commitments with Amazon Location Service,
and writes marked travel blocks back into the same calendar - while a Strands
agent on Amazon Bedrock proposes and deterministic code decides.**

## The gap is not free time

Picture a normal day: a 09:00 client visit in the City of London, an
appointment across the river at 11:00, and a school pickup at noon. The
calendar shows a clean hour between the first two, and another clean hour
between the second and third.

Neither gap was ever free. They are the drive, the parking, and the walk at the
other end. Calendars record commitments, not the journey between them, so the
person is left to be the integration layer: redo the arithmetic in your head
every time an appointment moves, and hope nothing else changed.

Glide is built for that gap. It reads the primary Google Calendar, estimates
driving time between physical appointments with Amazon Location Service, and
writes private, busy `Travel / Glide` blocks into the same calendar. When the
day changes, the blocks are reconciled. When a journey cannot fit, Glide does
not guess: it explains the shortfall and asks once.

![The Glide sample day: source appointments on a timeline with a reserved travel block between them](https://raw.githubusercontent.com/harveybellini/glide/main/submission/screenshots/02-timeline.png)

The hosted sample runs the whole workflow with fictional appointments and
simulated routes, so you can try it without an account, a Google grant, or any
model spend. The real Google and AWS path runs against a dedicated test
account, and the entry video walks through it.

## The design bet: the model proposes, deterministic code applies

Most agent failure stories start with a model that had more authority than it
needed. The central decision in Glide is a hard boundary between the two
halves of the system:

- A Strands agent, running on Amazon Bedrock (Amazon Nova 2 Lite through the
  `eu.amazon.nova-2-lite-v1:0` cross-region inference profile), receives a
  minimized, server-bound view of the day and exactly six typed tools.
- It has no write primitive. There is no `create_event` tool to misuse.
- It cannot invent the things that matter. Journey keys, occurrence ids, place
  ids, route durations, and feasibility results are all issued by the server,
  and every reference the model sends back is validated against them.
- Arithmetic is application code. Writes are application code. Permission to
  touch someone's calendar never leaves the executor.

That boundary is what makes Glide safe to run unattended: a confused model
produces a rejected proposal, not a mangled calendar. It is also what makes the
product quiet. The person is interrupted only when a decision genuinely needs
them, which is the "Agents for Humans" idea in practice.

### Six tools, one narrow job each

| Tool | What the model asks for | What the server owns |
| --- | --- | --- |
| `read_schedule` | the day inside a planning window | every journey key, occurrence id, and time |
| `lookup_place` | a location resolved from calendar text | candidates, provenance, confirmed aliases |
| `estimate_journey` | one timed driving route | the estimate id, duration, and observation time |
| `evaluate_candidate` | whether that journey fits | all arithmetic: feasibility, times, shortfall |
| `request_decision` | a human choice for one journey | a fixed reason and evidence vocabulary |
| `propose_plan` | one plan covering each journey once | acceptance, or a rejection code for repair |

When a location cannot be pinned down, the model does not guess. `lookup_place`
returns up to three candidates or one confirmed alias, and a journey with no
usable place becomes an `unknown_location` decision for the person. The same
pattern covers a missing start address (`unknown_start`) and a meeting that
might be in person or online (`hybrid_meeting`): the ambiguity is surfaced as a
decision with the evidence attached, rather than resolved by confident
improvisation.

The proposal schema is part of the boundary. An early version advertised
`update`, `noop`, and `skip` as proposal actions, but the validator only ever
accepted `create`, `remove`, and `decision` - the others are outcomes the
executor decides after comparing the plan with what is actually in the
calendar. The deployed model dutifully proposed the actions it was offered,
and the validator dutifully rejected them. The fix was not a better prompt; it
was deleting the impossible states from the schema:

```python
class PlannedJourney(ToolContract):
    """One journey in a model proposal."""

    model_config = ConfigDict(extra="forbid")

    journey_key: str
    origin_occurrence_id: str
    destination_occurrence_id: str
    # The only actions a model may propose. update/noop/skip are
    # executor outcomes, not model choices.
    action: Literal["create", "remove", "decision"]
    reason_code: str
    proposed_start: datetime | None = None
    proposed_end: datetime | None = None
    route_estimate_id: str | None = None
```

A tool schema is not just an interface. For an autonomous agent it is the
blast radius, so it should be smaller than you think you need.

Bounded loops are the other half of that safety. A run gets 24 turns and a
200-second wall-clock deadline, every turn must call a tool - a text-only turn
burns budget without changing anything - and a hook ends the loop the moment a
proposal is accepted. It also fails loudly: if the Bedrock model cannot be
built in production, the run errors rather than quietly falling back to a
deterministic planner, because an agent that claims model reasoning while
running something else is worse than one that stops.

### The arithmetic the model is not allowed to do

`evaluate_candidate` is deliberately boring. It takes the fixed duration the
route provider returned and does pure arithmetic against the free time around
the journey:

```python
required = travel_block_seconds(duration_seconds, padding_minutes)
available = max(
    (
        int((gap.end - gap.start).total_seconds())
        for gap in free_gaps(origin_available, destination_start, busy)
    ),
    default=0,
)
shortfall = max(required - available, 0)
```

It returns feasibility, the proposed start and end, and a quantified
shortfall, and the model must copy that result rather than compute its own.

Shortfall numbers are what keep a decision humane. "You need 40 minutes
including the 10-minute arrival buffer, there are 30 between these two
appointments, so this journey is 10 minutes short" is something a person can
act on. A model-invented number is not.

![A decision card showing a ten-minute travel shortfall and the actions that resolve it](https://raw.githubusercontent.com/harveybellini/glide/main/submission/screenshots/03-decision.png)

## What the writes actually look like

Once a proposal is accepted, the executor writes a private, busy, green
`Travel / Glide` event into the primary calendar. It writes only for journeys
it planned, touches only its own blocks, and identifies them with private
extended properties. Original appointments are never modified.

Writes are conditional: each update carries `If-Match` with the ETag of the
block it read, so a concurrent change fails safely with a `412` and is retried
instead of clobbered. Event ids are deterministic and scoped to the source
revision, and a `409` on create is recovered rather than duplicated - which
matters because Google reserves the ids of deleted events, so a fixed id
becomes a tombstone that can never be reused.

The behaviour that took the most care is the one nobody notices when it works:
the person can edit or delete a Glide block, and Glide treats that as a
decision. Move a travel block and the next run raises a `manual_edit` decision
(keep mine, replace with the plan, or skip the journey) instead of quietly
overwriting it. Delete one and it stays deleted until the source appointment
changes.

## Running while nobody is watching

A calendar agent that works only while a tab is open is a button, not an
agent. The background path runs on AWS:

| Service | Role in Glide |
| --- | --- |
| Amazon EventBridge | a rule every five minutes starts the dispatcher |
| AWS Lambda | the API (FastAPI through Mangum), the dispatcher, and the worker |
| Amazon SQS FIFO | one message group per person, so a user's checks stay in order |
| Amazon DynamoDB | single-table state: settings, runs, receipts, decisions, and one schedule pointer per tenant |
| Amazon Location Service | Places V2 `SearchText` and Routes V2 `CalculateRoutes` |
| Amazon Bedrock | the Strands agent loop over Amazon Nova 2 Lite |
| Amazon SES | the "needs your decision" email |
| Amazon CloudFront and Amazon S3 | the React front end served from a private bucket |
| AWS Secrets Manager | per-user Google tokens, refreshed in place |

On every tick the dispatcher enqueues a check only for tenants that are
watching and due. "Due" comes from one small pointer item per tenant rather
than a scan of run history, so the cost of the answer does not grow with the
account's age. Anonymous sample tenants get a 15-minute floor, a cap of three
new sessions per tick, and a 24-hour lifetime, so the public demo cannot
quietly run up a bill.

The worker reloads durable state at every job boundary instead of trusting a
warm container. A settings change during a run fences the stale result and
discards it. A cold worker rebuilds a sample session from its durable
snapshot. A result commits transactionally, so polling never observes a
terminal run with half of its records.

## Staying quiet by construction

The most delicate object in the product is an email.

Glide is meant to be quiet, but every scheduled run rebuilds the day's
decisions from scratch, which would erase the knowledge that the person was
already told about one. An "announce once" notification sitting on top of a
stateless rebuild is a bug waiting to happen, so the mark lives with the
decision and is explicitly carried forward:

```python
# Copy persisted marks onto the fresh decision objects a run just produced.
carry_notification_state(state_store, result)

# Send only unmarked, still-open decisions, then persist the mark.
# A failed send stays unmarked and is retried by the next scheduled check.
deliver_open_decisions(state_store, notifier, result)
```

The result is one email per decision, however many scheduled checks observe
it. A transport failure leaves the decision unmarked so the next check
retries, which is the safe direction to fail. The policy sits behind a single
adapter interface, so the SES transport can be joined by another channel
without touching the workflow. Email is opt-in, defaults to the address used
at sign-in, and can be paused or cleared.

## The defects only a real calendar could find

The offline suite was green long before the agent worked in production. The
live account found four problems that the fixtures had been happy to accept:

1. The proposal schema advertised actions the validator always rejected, so
   the model kept submitting proposals that could not be accepted.
2. Places resolved through `lookup_place` were rejected when passed to
   `estimate_journey`: the reference the model saw was not the reference the
   router accepted.
3. The model was asked to plan a start-address journey when no start address
   was configured, so it stalled. The server now issues an `unknown_start`
   decision instead of asking the model to guess.
4. After a turn-cap stop, the repair pass tried to continue a conversation
   that Bedrock refuses, so the retry failed too. Repair now starts a fresh
   agent.

A later live run exposed a timezone bug: a Glide block looked hand-edited on
every repeat, because the content hash compared a UTC write with a
London-offset read.

The scheduler had one of its own. The dispatcher's per-tick scan budget
covered only a quarter of the tenant table, so a freshly created sample could
wait twenty minutes for its first background check - while every tick reported
success and both queues were empty. The scan now covers a full pass per tick,
and a regression test pins a 1,200-item table against the old budget.

None of these were prompt problems. They were contract problems, and they only
appeared when a real model, a real calendar, and a real scheduler met.

## What we measured

Everything below was observed on the deployed stack in `eu-west-1` between 10
and 14 September 2026:

- 393 automated tests pass, alongside a clean lint run, frontend typecheck and
  production build, and 24 Playwright browser checks including the full judge
  path.
- Against a real Google account, the deployed agent wrote two travel blocks in
  20.7 seconds on the first live maintenance run, then completed ten
  consecutive runs in 10.3-15.5 seconds each (mean 11.3 seconds) with no
  failures.
- Repeats are idempotent: `unchanged` receipts and no duplicate events. A
  manually moved block raised a `manual_edit` decision instead of being
  overwritten, and a manually deleted block raised `manually_deleted` instead
  of being recreated.
- A run scheduled with the browser closed reached a terminal state on its own,
  and the decision email was delivered from a verified Amazon SES identity
  with a durable `notified_at` stamp, so an unresolved repeat sent nothing.
- A fresh hosted sample received its first scheduled check 56-123 seconds
  after creation with no browser open, and the page footer, `/version.json`,
  and `/api/health` all reported the same build.

The limitations belong in the same list. The SES account is still in the
sandbox, so mail currently reaches verified recipients only. The hosted sample
uses simulated routes so that judges can run the loop for free. Usability
testing with unfamiliar users is still ahead of us.

## What we would build next

The notification seam is the obvious next step: a Slack MCP server that
delivers the same "needs your decision" card as a direct message with inline
approve and skip actions, so answering never requires opening the app. After
that: walking and public transport alongside driving, additional calendars,
and departure alerts once delivery testing is done.

## Try it

- Hosted sample, no account needed (fictional appointments, simulated
  routes): <https://d3tvxy281s2u11.cloudfront.net>
- Source, setup, and architecture:
  <https://github.com/harveybellini/glide>

![Glide architecture: a React front end on CloudFront and S3; API Gateway and Lambda for the API; an EventBridge dispatcher, an SQS FIFO queue, and a worker Lambda; DynamoDB for state; Amazon Bedrock, Amazon Location Service, Google Calendar, and Amazon SES](https://raw.githubusercontent.com/harveybellini/glide/main/docs/architecture.png)

If you are building an agent that touches a real person's data, the boundary
is the product. Let the model do the reasoning, keep the arithmetic, the
identity, and the write in deterministic code, and interrupt the human only
when there is a decision worth their attention.

**Tags:** Agents for Humans, Amazon Bedrock, Strands Agents, Amazon Location
Service, Amazon EventBridge, Amazon SQS, Amazon DynamoDB, Amazon SES, Google
Calendar, serverless
