# Video script — target 4 minutes 30 seconds

Record with real accounts. Bedrock, Amazon Location, Google Calendar, the
deployed pipeline, and ten consecutive live runs already have evidence
(`docs/evaluation.md`). The Google test account was disconnected after the
proof run, so click **Connect Google Calendar** once at the start - that is the
natural opening shot. Actual values, not fixtures, appear on screen. Cut any
beat whose evidence is missing.

## 0:00–0:25 — Problem

Show a real day with a 09:00 client visit, an 11:00 appointment, and a 12:00
school pickup. Point at the gaps: "These look free, but they are exactly the
time this person spends driving." One appointment moves, and the mental
recalculation begins again.

## 0:25–0:50 — What Glide is

Driving time plus an arrival buffer, reserved directly in the user's primary
calendar as app-owned travel blocks. Source appointments are never edited.
Automation can be paused.

## 0:50–1:40 — Real run

Connect Google Calendar (test account, fictional names), enable Glide, run a
check. Show the source read, the real Amazon Location route, the Strands tool
sequence, and one `Travel · Glide` block appearing in the primary calendar.

## 1:40–2:35 — Conflict and decision

Set the appointments so the next journey needs more time than exists. Show
the quantified shortfall and the **Needs your decision** card. Edit the
appointment, recheck, and show the block now fitting.

Optional beat (only if a verified Amazon SES sending identity and a
`notification_email` on the tenant exist): as the shortfall appears, the phone
shows the once-only email: name the problem, one link. Open it and land on this
same card, outlined. Say that this is the only time Glide interrupts, and that
a repeat check stays quiet. Otherwise say it in one sentence without showing
the phone.

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
three-step judge flow and state that no Google account is needed.

## 4:05–4:30 — Close

Name, repository, live demo link, and roadmap: the same decision card as a
Slack direct message with inline actions, then walking/transit, additional
calendars, and departure alerts after delivery.
