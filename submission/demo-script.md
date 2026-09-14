# Video script — target 4 minutes 30 seconds

Record with real accounts. Bedrock, Amazon Location, Google Calendar, the
deployed pipeline, Amazon SES, and ten consecutive live runs already have
evidence (`docs/evaluation.md`). Actual values, not fixtures, appear on the
live beats; the sample beat is explicitly fictional.

The decision email is a required beat, not an optional one. Record it from a
real shortfall with the notification address set in Settings, and capture the
inbox screenshot, the message id, and the deep link opening the highlighted
card (`docs/live-proof-runbook.md`, step 5). The hosted sample cannot send
mail: anonymous sample sessions are provider-free, so the video is where
viewers see this part of the product. The email beat adds about twenty
seconds; keep the maintenance and architecture beats to one sentence each to
stay under five minutes.

The tenant must start from an open decision that has already been notified.
Create the recording conflict by changing an appointment: the new source
revision makes a new decision id, so SES sends a fresh message, while an
already-notified card stays silent by design.

## Before recording

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
- Seed the fictional day in the primary calendar inside the day view's live
  window (an hour behind now to 48 hours ahead) — earlier appointments are not
  shown — and delete the leftover manually edited `Travel / Glide` block from
  the proof run.
- Sign in to the site on the phone used for the email beat. The deep link only
  outlines the card when that device already has a session.
- Run `scripts/verify_deployed_sample.py` once after the deploy and keep its
  output. It proves the hosted sample starts watching and checks itself with
  no browser action; no hosted claim is valid until it passes.
- Confirm the footer reports the same build for the tab and the host, and have
  CloudWatch Logs open on the worker's log group for the tool-trace cutaway.

## 0:00–0:25 — Problem

Show a real day with a 09:00 client visit, an 11:00 appointment, and a 12:00
school pickup. Point at the gaps: "These look free, but they are exactly the
time this person spends driving." One appointment moves, and the mental
recalculation begins again.

## 0:25–0:50 — What Glide is

Driving time plus an arrival buffer, reserved directly in the user's primary
calendar as app-owned travel blocks. Source appointments are never edited.
The agent watches in the background on its own schedule; when a decision is
waiting Glide sends a notification to the user, otherwise it stays quiet. No one opens an app
to make it check.

## 0:50–1:40 — Real run

Start in the live day view. If you had to reconnect, click **Connect Google
Calendar** first, then press **Start watching** (a fresh connect arrives
paused); otherwise the strip already says it is watching — point at that
instead. Start watching queues the first check immediately.

Show the source read — the appointments in the timeline — and one Glide travel
block appearing in the primary calendar. The block is the drive plus the
arrival buffer shown in Settings; point at its start and end times. Then cut to
CloudWatch Logs and show the worker's tool lines for this run
(`tool=read_schedule`, `lookup_place`, `estimate_journey`, `propose_plan`),
labelled as the worker's trace: the Strands sequence is deliberately kept out
of the everyday interface. If you want the route number on screen, take it
from the block's length or a labelled `scripts/live_smoke.py` cutaway — never
by implying the UI shows provider payloads.

Point at the status strip: last check, next check, and the cadence. The next
check appears after the dispatcher's following tick (up to five minutes), so
record this with watching already on. Close the tab to show nothing stops,
then reopen it to show the strip reporting what ran while it was closed.

## 1:40–2:35 — Conflict and decision

Edit the appointment in Google Calendar — the in-app **Edit** control only
appears for sample events — so the next journey needs more time than exists.
Show the quantified shortfall and the **Needs your decision** card. As the
shortfall appears, the phone shows the decision email arriving from Amazon
SES: name the shortfall, and show that there is one link and no marketing.
Tap it (sign in on that phone first) and land on this same card, outlined for
about six seconds, still signed in. Say that this is the only time Glide
interrupts, and that an unresolved repeat check stays quiet because the
decision is already marked as notified. If the inbox is slow, keep the camera
on the card and cut the arrival shot in at the moment the message lands.

Then resolve it: edit the appointment in Google Calendar, recheck, and show the
block now fitting. Alternatively use **Add it anyway** on a fresh card: the
block appears without any calendar edit, arriving exactly as the appointment
starts, and you can type the reason.

## 2:35–3:05 — Maintenance

Once the day has settled, repeat the check: Activity shows `unchanged`, no
duplicates. Move one appointment again in Google Calendar: the old block
updates and the following journey recalculates. Delete it: the obsolete block
is removed.

## 3:05–3:35 — Architecture

Show `docs/architecture.png`: browser → CloudFront → API; EventBridge →
dispatcher → SQS FIFO → worker; DynamoDB, Bedrock, Amazon Location, Google.
Amazon SES sits beside the worker for the decision email. One sentence each;
keep SDK traces out of the everyday interface.

## 3:35–4:05 — Judge path

Open the labelled sample (`Sample calendar - simulated routes`). Walk the
judge flow and state that no Google account is needed. The sample starts
watching by itself, so the decision card can appear without anyone pressing
anything — the dispatcher ticks every five minutes, so allow a few minutes for
the first scheduled card; **Recheck now** exists only to make a judge wait
less. The status strip counts the checks that ran while the tab was closed.
The guided tour is the faster version of this beat: it spotlights each control
in turn, and **Show me around** replays it.

## 4:05–4:30 — Close

Name, repository, live demo link, and roadmap: the same decision card as a
Slack direct message with inline actions, then walking/transit, additional
calendars, and departure alerts after delivery.

## Email evidence to capture while recording

- The message arriving in the inbox, with sender and subject visible.
- The message id and headers from the received message.
- The link opening `/?decision=<id>` and outlining the same card.
- An unresolved repeat check: no second email arrives.
- The worker's tool lines for that run's `run_id`, if the trace cutaway is
  included.
