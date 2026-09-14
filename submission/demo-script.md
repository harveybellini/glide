# Video script — target 4 minutes 25 seconds

Judges score five things: technical implementation (Strands Agents SDK), design,
potential impact, originality, and presentation. Every beat below names what is
on screen and what is said, because the pitch is scored on clarity as much as
the demo. The captions and lower-thirds carry the claims, so the video still
lands when it is watched muted. The last section is the fallback list for the
beats that depend on timing you do not control.

Record with real accounts. Bedrock, Amazon Location, Google Calendar, the
deployed pipeline, Amazon SES, and ten consecutive live runs already have
evidence (`docs/evaluation.md`). Actual values, not fixtures, appear on the
live beats; the sample beat is explicitly fictional and the hosted sample is
provider-free — deterministic routes, no model spend — so never imply it runs
Bedrock. The timings total 4:25, which leaves 35 seconds of headroom under the
five-minute limit for a title card and any overrun.

The decision email is a required beat, not an optional one. Record it from a
real shortfall with the notification address set in Settings, and capture the
inbox screenshot, the message id, and the deep link opening the highlighted
card (see "Decision emails" in `docs/setup.md`). The hosted sample cannot send
mail: anonymous sample sessions are provider-free, so the video is where
viewers see this part of the product. The email beat adds about twenty
seconds; keep the maintenance and architecture beats to one sentence each to
stay inside the budget.

The tenant must start from an open decision that has already been notified.
Create the recording conflict by changing a source appointment: the new
source revision makes a new decision id, so SES sends a fresh message, while
an already-notified card stays silent by design. Editing one of Glide's own
`Travel · Glide` blocks raises a manual-edit decision instead — that is the
maintenance beat, not the email beat.

## Before recording

- Start watching at least one dispatcher tick before you roll — up to five
  minutes, so give it ten to be safe. The strip only shows a next check once a
  tick has written the pointer, and the next check itself is one interval
  (15 minutes by default) after that tick.
- Confirm the tenant in Settings: Google Calendar **connected**, the strip
  saying it is watching, the notification address set, and decision emails on.
  A fresh connect arrives paused — press **Start watching**, which also queues
  the first check immediately. Reconnect only if Settings shows it
  disconnected; Testing-mode Google grants can expire after seven days.
- SES is still in the sandbox, so the notification address must be a verified
  recipient.
- Confirm one open decision is already stamped as notified, then change the
  source appointment to raise a fresh one. If there is no open notified
  decision, raise and send one first rather than narrating it.
- On the phone used for the email beat: signed in to the site (the deep link
  only outlines the card when that device already has a session), mailbox open
  with sender and subject visible, and the thread's message count written
  down. The "no second email" shot is a comparison, not a claim.
- Seed the fictional day in the primary calendar inside the day view's live
  window (an hour behind now to 48 hours ahead) — earlier appointments are not
  shown — and delete the leftover manually edited `Travel · Glide` block from
  the proof run.
- Run `scripts/verify_deployed_sample.py` once after the deploy and keep its
  output. It proves the hosted sample starts watching and checks itself with
  no browser action; no hosted claim is valid until it passes.
- Confirm the footer reports the same build for the tab and the host, and have
  CloudWatch Logs open on the worker's log group for the tool-trace cutaway.

## 0:00–0:30 — Problem, audience, and why it matters

**On screen:** a real day with a 09:00 client visit, an 11:00 appointment, and
a 12:00 school pickup. Point at the gaps; drag one appointment.

**Say:** "This is a Wednesday for anyone whose day is appointments in
different places — a rep doing client visits, a carer, a parent who has to be
back at the school gate at twelve. These gaps look free. They are exactly the
time this person spends driving. Move one appointment, and the whole
calculation is wrong again."

**Caption:** "The work nobody counts: re-checking the day."

Close the beat with the why-it-matters line: "Re-checking it is the work
nobody counts — and it is the work Glide takes."

## 0:30–0:55 — What Glide is

**On screen:** Settings first — the arrival buffer, the strip saying it is
watching, the notification address. Then the day view: a Glide `Travel ·
Glide` block beside an untouched source appointment.

**Caption:** "app-owned block · source appointment never edited."

**Say:** "Glide adds the drive, plus your arrival buffer, to your own calendar
as private travel blocks marked as Glide's. Your appointments are never
edited. The agent checks on its own schedule, stays silent when nothing is
wrong, and reaches you when a decision is waiting. Nobody opens an app to make
it check."

## 0:55–1:45 — Real run

**On screen:** the live day view. If you had to reconnect, click **Connect
Google Calendar** first, then press **Start watching** (a fresh connect
arrives paused, and Start watching also queues the first check immediately);
otherwise the strip already says it is watching — point at that instead.

Show the source read — the appointments in the timeline — and one Glide travel
block appearing in the primary calendar. The block is the drive plus the
arrival buffer shown in Settings; point at its start and end times.

Then cut to the worker's trace as a labelled overlay rather than a scrolling
CloudWatch pane, which nobody can read at video bitrate. The overlay lists the
six tools on the run's `run_id`: `read_schedule`, `lookup_place`,
`estimate_journey`, `evaluate_candidate`, `request_decision`, `propose_plan`.

**Caption:** "Strands Agents SDK · Amazon Bedrock (Nova Lite, eu-west-1) · six
typed tools."

**Say over the overlay:** "Six typed tools on the Strands Agents SDK, running
on Amazon Bedrock. The model reads the day and asks Amazon Location for a real
drive — but it never does the arithmetic and it never writes to the calendar.
One tool returns the feasibility and the shortfall, one raises a single
deduplicated decision, and the plan it submits is only a proposal:
deterministic code validates before anything is written."

Point at the status strip: last check, next check, and the cadence. The next
check appears after the dispatcher's following tick (up to five minutes),
which is why you record with watching already on. Close the tab to show
nothing stops, then reopen it to show the strip reporting what ran while it
was closed.

If you want the route number on screen, take it from the block's length or a
labelled `scripts/live_smoke.py` cutaway — never by implying the UI shows
provider payloads.

## 1:45–2:40 — Conflict and decision

**On screen:** edit the source appointment in Google Calendar — the in-app
**Edit** control only appears for sample events — so the next journey needs
more time than exists. Show the quantified shortfall and the **Needs your
decision** card. As the shortfall appears, the phone shows the decision email
arriving from Amazon SES: name the shortfall, and show that there is one link
and no marketing. Tap it (sign in on that phone first) and land on this same
card, outlined for about six seconds, still signed in.

**Say:** "The journey no longer fits. Glide quantifies the shortfall instead
of guessing — and this is the only moment it interrupts. One message, one
link, no marketing."

Then, over the card: "An unresolved decision stays quiet next time. It is
already marked as notified, so the message never repeats."

Resolve it: edit the source appointment, recheck, and show the block now
fitting. Alternatively use **Add it anyway** on a fresh card: the block appears
without any calendar edit, arriving exactly as the appointment starts, and you
can type the reason.

If the inbox is slow, keep the camera on the card and cut the arrival in at
the moment the message lands — never present a replay as live.

## 2:40–3:10 — Maintenance

**On screen:** repeat the check — Activity shows `unchanged`, no duplicates.
Then cut to the inbox: same one message, same count and timestamp. Then move
one appointment in Google Calendar: the old block updates and the following
journey recalculates. Delete it: the obsolete block is removed.

**Say, before the first check:** "The interesting part is not that it plans a
drive once. It maintains the day — and it knows when to defer. Move one of its
blocks by hand and Glide raises a decision instead of overwriting you."

**Say, over the inbox:** "Same open decision, checked again — nothing new,
because the message was already sent once."

## 3:10–3:30 — Architecture

**On screen:** one labelled pass over `docs/architecture.png`: browser →
CloudFront → API; EventBridge → dispatcher → SQS FIFO → worker; DynamoDB,
Bedrock, Amazon Location, Google. Amazon SES sits beside the worker for the
decision email.

**Say:** one sentence naming Strands on Bedrock, the scheduler and queue that
keep it checking while the browser is closed, and the single message SES
sends. Keep SDK traces out of the everyday interface.

## 3:30–3:52 — Judge path

**On screen:** the labelled sample (`Sample calendar - simulated routes`),
recorded as the guided tour rather than a live walk. It spotlights each
control in turn, and **Show me around** replays it.

**Say:** "The hosted sample needs no Google account and starts watching by
itself. It is simulated and provider-free — deterministic routes, no model
spend — so you can walk the whole flow. **Recheck now** exists only so you do
not wait for the dispatcher's five-minute tick. Everything you just saw on the
live path is what the real Google and AWS integration does."

The sample starts watching on creation and the first scheduled card arrives on
a dispatcher tick (up to five minutes); the status strip counts the checks
that ran while the tab was closed.

## 3:52–4:25 — Close

**On screen:** end card with the project name, repository, and live demo link,
held for at least five seconds so it can be read and clicked from the video.

**Say:** "Today the decision arrives as a transactional email. Next, a Slack
MCP server delivers the same card as a direct message with the actions inline
— so the notification is seamless: it arrives in a conversation you already
have open, and you answer without opening anything. The policy seam is already
transport-agnostic: one decision, marked once, sent once. After that: walking
and transit, additional calendars, and departure alerts. Glide — [repository],
live at [demo URL]."

## Email evidence to capture while recording

- The message arriving in the inbox, with sender and subject visible.
- The message id and headers from the received message.
- The link opening `/?decision=<id>` and outlining the same card.
- The unresolved repeat: the inbox count and timestamp before and after the
  repeat check, showing no second message (film the before-and-after
  comparison deliberately).
- The worker's tool lines for that run's `run_id`, if the trace cutaway is
  included.

## If a beat does not behave

- No fresh decision after the edit: confirm the edit landed inside the day
  window, and that you changed a source appointment rather than one of
  Glide's own blocks.
- The email has not arrived: keep the camera on the card, keep talking, and
  cut the genuine arrival in at its real timestamp.
- The strip shows no next check: it needs one dispatcher tick to write the
  pointer. Wait for the tick rather than narrating a value that is not on
  screen.
- The trace is noisy: keep the cutaway on the terminal lines for the run's
  `run_id` and let the overlay carry the tool names.
