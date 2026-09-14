## Inspiration

Calendars record when commitments happen, but the journey between them often
remains a mental calculation. On a day with client visits, an appointment,
and a school pickup, an empty gap can look available even when it is needed
for driving. A change to one appointment means checking the whole day again.

Glide is built around a simple idea: the calendar should include the time
needed to get there. It is for anyone whose day is appointments in different
places - a rep doing client visits, a carer, a parent who has to be back at
the school gate at twelve - where the gaps look free but are exactly the time
they spend driving. Re-checking that day is the work nobody counts.

## What it does

Glide connects to Google Calendar, reads upcoming appointments, and creates
driving-time blocks directly in the user's primary calendar. It combines
route estimates with the user's arrival buffer and checks each journey against
the commitments around it.

When an appointment moves or disappears, Glide updates its blocks. When
travel cannot fit, it explains the shortfall and lets the person correct a
location, skip the journey, edit the appointment and recheck, or add the
journey anyway with a note. Users can pause automation and stay in control of
their original appointments.

Glide watches in the background on its own schedule - every 15 minutes by
default - and the day view shows what it has been doing: the last check, the
next one, and how many ran while the tab was closed. The page refreshes
itself, so a decision the agent raised while nobody was looking is waiting
when the person returns. Glide stays quiet otherwise, but it reaches the
person when a decision is waiting: a short transactional email names the
shortfall and links straight back to the highlighted decision card. Each
decision is announced exactly once, however many scheduled checks re-observe
it.

The first version covers driving and one source calendar. The hosted sample
at <https://d3tvxy281s2u11.cloudfront.net> lets judges walk the whole workflow
with fictional appointments and simulated routes: no account, no fees, and no
model spend. It starts watching on creation and completes its first
background check with no browser action. The entry video covers the real
Google and AWS integrations.

## How we built it

The Strands Agents SDK connects calendar context, place lookup, route
estimation, and structured journey proposals through six typed tools.
Amazon Bedrock (`eu.amazon.nova-2-lite-v1:0` in `eu-west-1`) provides the
model, and Amazon Location Service provides place and driving-route
information. Deterministic application code validates time constraints and
limits writes to Glide's own calendar events.

An AWS scheduler and FIFO queue keep the application checking while the
browser is closed. A tenant is only queued while it is watching and due, with
a per-tenant interval pointer; anonymous samples carry a 15-minute floor and
a three-per-tick cap, and sample runs use the deterministic planner, so the
public demo cannot generate model spend. Persistent state connects
appointments to their travel blocks, so retries and schedule changes
reconcile without duplicates, and user-edited or deleted blocks are respected
rather than overwritten.

Amazon SES delivers the "needs your decision" email from a verified sending
identity, and the once-only mark is persisted with the decision so a rerun,
retry, or cold worker never repeats a message. Every page also reports the
build it is running and compares it with the deployed manifest and the API's
version, so a stale tab offers a reload instead of quietly running old code.

![Glide architecture: React on CloudFront and S3; API Gateway and Lambda for the API; an EventBridge dispatcher, SQS FIFO queue, and worker Lambda; DynamoDB state; Amazon Bedrock, Amazon Location Service, Google Calendar, and Amazon SES](https://raw.githubusercontent.com/harveybellini/glide/main/docs/architecture.png)

## Accomplishments

Verified against the deployed stack between 11 and 14 September 2026,
including the owner's live Google account:

- The browser workflow passes the full judge path end to end: sample day, a
  quantified ten-minute shortfall, resolve, recheck with two updated blocks
  and no duplicates, reset, a skip that persists across checks, and an
  add-anyway override with an optional note.
- Against the owner's real Google account the deployed agent wrote two
  `Travel / Glide` blocks in 20.7 seconds, then completed ten consecutive
  maintenance runs in 10.3-15.5 seconds each with no failures. Moving a Glide
  block raised a `manual_edit` decision instead of overwriting it, deleting one
  raised a `manually_deleted` decision instead of recreating it, and a run
  scheduled with the browser closed finished on its own.
- 393 automated tests pass alongside lint, typecheck, a production build, and
  24 Playwright browser checks, including the full judge path.
- The hosted sample is verified on the deployed build (0.4.2): a fresh sample
  starts watching on creation, its first scheduled check arrived 56-123
  seconds after session creation with no browser open, its travel blocks
  survived that check, and the bundle, `/version.json`, and `/api/health` all
  report the same version.
- The background agent is visible, not implied: the day response carries its
  watching state, interval, last and next check, and a "while you were away"
  count read from the dispatcher's durable pointer, and the page polls while
  visible and refetches on focus. A fresh sample starts watching on creation;
  a connected Google account starts paused and watches only after the owner
  presses **Start watching**. The dispatcher enforces the interval ceiling,
  the sample floor and per-tick cap, and the 24-hour snapshot lifetime.
- Google sign-in is wired to the workflow end to end: PKCE plus a
  browser-bound, single-use state, stored and refreshed tokens, and one API
  surface serving both signed-in users and isolated sample sessions. Tests
  prove a second user cannot read or change the first user's settings, runs,
  decisions, or activity.
- Calendar writes are conditional (`If-Match`), use deterministic
  revision-scoped event ids with 409 recovery, and never edit source
  appointments. User-edited blocks are preserved on every removal path, and
  a manually deleted block stays deleted until its source revision changes.
- Scheduled background processing works across instances: the dispatcher
  writes a queued run row before enqueueing, cold workers restore durable
  sample sessions, expired tenants stop generating work, and a settings
  change during a run discards the stale result instead of committing it.
- Decision notifications are once-only by construction: a decision is marked
  when Amazon SES accepts the message, the mark is carried across the fresh
  decision objects every run rebuilds, and a transport failure leaves the
  decision unmarked so the next scheduled check retries it. Email is
  opt-in per user, defaults to the address used at Google sign-in, and can be
  paused or cleared in Settings. The SES account is still in the sandbox, so
  mail currently reaches verified recipients only; the provider-free sample
  never sends mail.

## Challenges we ran into

An implementation review reproduced fourteen defects before any live
account was used, each now fixed with a regression test:

- The installed Google client's `execute()` does not accept a headers
  argument, so the original conditional-write calls failed before reaching
  Google. `If-Match` is now set on the request headers before `execute()`,
  checked against the official Calendar guide.
- The OAuth transaction lost its PKCE verifier and its initiating browser.
  Transactions are now encrypted, path-scoped, single-use cookies, and event
  writes target the user's primary calendar with consent, identifying
  Glide-owned blocks by private extension properties.
- A worker instance could not rebuild a sample session, discard scheduled
  results, or ignore newer persisted state; the deployed API also
  initialized local SQLite at import. Session state is now restored from
  DynamoDB on every access, scheduled jobs persist a run row first, and
  production imports build no local database.
- Manually edited blocks could be deleted and manually deleted blocks could
  be recreated on the next run. Ownership checks, manual-override guards,
  and revision-scoped skips now make those choices durable.

The live planner took four real defects to stabilize, each found by running
the deployed worker against the real calendar rather than a fixture: the
proposal schema advertised actions the validator always rejects, places
resolved through `lookup_place` were not acceptable to `estimate_journey`, the
model was asked to decide a start-address journey that had no start address,
and the repair pass tried to continue a conversation Bedrock refuses after a
turn-cap stop. With those fixed, the first live run completed in 20.7 seconds
and wrote two travel blocks; ten consecutive live runs then completed in
10.3-15.5 seconds each, repeats were idempotent, manually edited and manually
deleted blocks were respected, and a scheduled run finished with the browser
closed. A separate timezone bug made Glide's own blocks look hand-edited on
every repeat, because the content hash compared a UTC write with a
London-offset read.

Live verification then caught a scheduling defect of its own: the
dispatcher's per-tick scan budget covered only a quarter of the tenant table,
so a freshly created sample could wait twenty minutes for its first
background check even though every tick had run without error and the queues
were empty. The scan now covers a full pass per tick, and a regression test
pins a 1,200-item table against the old budget.

## What we learned

Provider SDK contracts cannot be proven by permissive fakes: a test that
accepts unsupported keyword arguments passed while the real library would
have failed. The adapter tests now exercise the actual request-object
behavior and the documented error codes.

Idempotency is a provider-level problem, not just a database one. Google
keeps the ids of deleted events reserved, so deterministic ids become a
tombstone; scoping those ids to the source revision is what allows a
deliberate reopen. Much of the reliable behavior came from keeping
deterministic code in control of identity, arithmetic, and writes, and from
re-reading durable state at every job boundary instead of trusting warm
caches.

## What's next for Glide

Extend the tested driving workflow to walking and public transport, add
per-journey preferences where coverage supports them, include additional
calendars, and improve departure alerts after delivery testing.

With more time we would meet people where they already are: a Slack MCP server
that delivers the same "needs your decision" card as a direct message with the
approve/skip actions inline, so the notification is seamless - it arrives in a
conversation that is already open, and the decision never requires opening the
web app. The notification policy is already transport-agnostic - one
once-only decision mark, one adapter interface - so the Slack adapter sits
beside the SES adapter rather than changing the workflow. The same seam
covers quiet hours and per-channel preferences.

## Try it

- Hosted sample, no account needed:
  <https://d3tvxy281s2u11.cloudfront.net> - fictional appointments and
  simulated driving times.
- Source and setup instructions:
  <https://github.com/harveybellini/glide>
- The live Google Calendar and Amazon Bedrock/Amazon Location path runs
  against the owner's test account and is shown in the entry video.

## Built With

`strands-agents-sdk`, `python`, `amazon-bedrock`, `amazon-location-service`,
`google-calendar-api`, `amazon-ses`, `aws-lambda`, `amazon-eventbridge`,
`amazon-sqs`, `amazon-dynamodb`, `amazon-s3`, `amazon-cloudfront`,
`amazon-api-gateway`, `react`, `typescript`, `fastapi`, `aws-sam`.
