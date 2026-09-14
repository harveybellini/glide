# Video script — target 4 minutes 30 seconds

Record with real accounts. Bedrock, Amazon Location, Google Calendar, the
deployed pipeline, Amazon SES, and ten consecutive live runs already have
evidence (`docs/evaluation.md`). The owner's Google test account is connected
and enabled, so the day view opens on the live calendar; reconnect only if
Settings shows it disconnected. Actual values, not fixtures, appear on screen.

The decision email is a required beat, not an optional one. Record it from a
real shortfall with the notification address set in Settings, and capture the
inbox screenshot, the message id, and the deep link opening the highlighted
card (`docs/live-proof-runbook.md`, step 5). The hosted sample cannot send
mail: anonymous sample sessions are provider-free, so the video is where
viewers see this part of the product. The email beat adds about twenty
seconds; keep the maintenance and architecture beats to one sentence each to
stay under five minutes.

The tenant already has an open decision that has been notified, so create the
recording conflict by changing an appointment: the new source revision makes
a new decision id and SES sends a fresh message. An already-notified card
stays silent by design.

## 0:00–0:25 — Problem

Show a real day with a 09:00 client visit, an 11:00 appointment, and a 12:00
school pickup. Point at the gaps: "These look free, but they are exactly the
time this person spends driving." One appointment moves, and the mental
recalculation begins again.

## 0:25–0:50 — What Glide is

Driving time plus an arrival buffer, reserved directly in the user's primary
calendar as app-owned travel blocks. Source appointments are never edited.
The agent watches in the background on its own schedule; when a decision is
waiting Glide sends one email, otherwise it stays quiet. No one opens an app
to make it check.

## 0:50–1:40 — Real run

Connect Google Calendar (test account, fictional names), press **Start
watching**, and let the first check run. Show the source read, the real
Amazon Location route, the Strands tool sequence, and one Glide travel
block appearing in the primary calendar. Point at the status strip: last
check, next check, and the cadence. Close the tab to show nothing stops,
then reopen it to show the strip reporting what ran while it was closed.

## 1:40–2:35 — Conflict and decision

Set the appointments so the next journey needs more time than exists. Show
the quantified shortfall and the **Needs your decision** card. As the
shortfall appears, the phone shows the decision email arriving from Amazon
SES: name the shortfall, and show that there is one link and no marketing.
Tap it and land on this same card, outlined, still signed in. Say that this is
the only time Glide interrupts, and that an unresolved repeat check stays
quiet because the decision is already marked as notified. If the inbox is
slow, keep the camera on the card and cut the arrival shot in at the moment
the message lands.

Then resolve it: edit the appointment, recheck, and show the block now
fitting. Alternatively use **Add it anyway** on a fresh card: the block
appears without any calendar edit, arriving exactly as the appointment starts,
and you can type the reason.

## 2:35–3:05 — Maintenance

Repeat the check: Activity shows `unchanged`, no duplicates. Move one
appointment again: the old block updates and the following journey
recalculates. Delete it: the obsolete block is removed.

## 3:05–3:35 — Architecture

Show `docs/architecture.png`: browser → CloudFront → API; EventBridge →
dispatcher → SQS FIFO → worker; DynamoDB, Bedrock, Amazon Location, Google.
Amazon SES sits beside the worker for the decision email. One sentence each;
keep SDK traces out of the everyday interface.

## 3:35–4:05 — Judge path

Open the labeled sample: "Sample calendar · simulated routes." Walk the
judge flow and state that no Google account is needed. The sample starts
watching by itself, so the decision card can appear without anyone
pressing anything; **Recheck now** exists only to make a judge wait less.
The status strip counts the checks that ran while the tab was closed. The
guided tour is the faster version of this beat: it spotlights each control
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
