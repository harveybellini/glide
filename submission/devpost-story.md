# Glide — project story

> Publication gate: replace placeholders with measured results before
> submitting. Present-tense claims below must match shipped behavior.

## Inspiration

Calendars record when commitments happen, but the journey between them often
remains a mental calculation. On a day with client visits, an appointment,
and a school pickup, an empty gap can look available even when it is needed
for driving. A change to one appointment means checking the whole day again.

Glide is built around a simple idea: the calendar should include the time
needed to get there.

## What it does

Glide connects to Google Calendar, reads upcoming appointments, and creates
driving-time blocks directly in the user's primary calendar. It combines
route estimates with the user's arrival buffer and checks each journey against
the commitments around it.

When an appointment moves or disappears, Glide updates its blocks. When
travel cannot fit, it explains the shortfall and lets the person correct a
location, skip the journey, or edit the appointment and recheck. Users can
pause automation and stay in control of their original appointments.

Glide stays quiet the rest of the time, but it does reach the person when a
decision is waiting: a short transactional email names the shortfall and
links straight back to the highlighted decision card. Each decision is
announced exactly once, however many scheduled checks re-observe it.

The first version covers driving and one source calendar. A hosted sample
lets judges explore the workflow with fictional appointments and simulated
routes; the entry video covers the real Google and AWS integrations.

## How we built it

The Strands Agents SDK connects calendar context, place lookup, route
estimation, and structured journey proposals through six typed tools.
Amazon Bedrock (`eu.amazon.nova-2-lite-v1:0` in `eu-west-1`) provides the
model, and Amazon Location Service provides place and driving-route
information. Deterministic application code validates time constraints and
limits writes to Glide's own calendar events.

An AWS scheduler and FIFO queue keep the application checking while the
browser is closed. Persistent state connects appointments to their travel
blocks, so retries and schedule changes reconcile without duplicates, and
user-edited or deleted blocks are respected rather than overwritten.
Amazon SES delivers the "needs your decision" email from a verified sending
identity, and the once-only mark is persisted with the decision so a rerun,
retry, or cold worker never repeats a message.

## Accomplishments

Offline-verified as of 11 September 2026 (real-provider measurements are
recorded only after the account setup below is complete):

- The browser workflow passes four end-to-end judge-path checks: sample day,
  conflict decision with a quantified ten-minute shortfall, resolve, recheck
  with two updated blocks and no duplicates, and reset. A repeat run records
  an `unchanged` receipt instead of a second calendar event.
- 323 automated tests pass alongside lint, typecheck, and a production
  build. The recovery matrix covers a worker crash after the first provider
  write, idempotent reruns, cross-tenant run access returning 404, and a
  padding change updating the block on recheck.
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
  paused or cleared in Settings.

Usability observations from two fresh-browser passes through the sample
flow (unfamiliar testers) belong here only once re-run against the deployed
release.

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

The Google account is connected, but the deployed planner has not yet
produced an accepted proposal: every live run so far ended
`AgentProposalMissing` or stayed queued, so the first real calendar write is
still outstanding. A prompt and turn-budget fix is in the working tree,
pending a redeploy and re-verification. Ambiguous venues, OAuth test-token
expiry, and timeout retries remain to be measured against real providers and
will be added here with their outcomes.

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

With more time we would meet people where they already are: a Slack bot that
delivers the same "needs your decision" card as a direct message with the
approve/skip actions inline, so the decision never requires opening the web
app at all. The notification policy is already transport-agnostic — one
once-only decision mark, one adapter interface — so a Slack adapter sits
beside the SES adapter rather than changing the workflow. The same seam
covers quiet hours and per-channel preferences.

## Built With

`strands-agents-sdk`, `python`, `amazon-bedrock`, `amazon-location-service`,
`google-calendar-api`, `amazon-ses`, `aws-lambda`, `amazon-eventbridge`,
`amazon-sqs`, `amazon-dynamodb`, `amazon-s3`, `amazon-cloudfront`,
`amazon-api-gateway`, `react`, `typescript`, `fastapi`, `aws-sam`.
