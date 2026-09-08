# Video script — target 4 minutes 30 seconds

Record with real accounts only after the live proof exists. Actual values,
not fixtures, appear on screen. Cut any beat whose evidence is missing.

## 0:00–0:25 — Problem

Show a real day with a 09:00 client visit, an 11:00 appointment, and a 12:00
school pickup. Point at the gaps: "These look free, but they are exactly the
time this person spends driving." One appointment moves, and the mental
recalculation begins again.

## 0:25–0:50 — What Glide is

Driving time plus an arrival buffer, reserved in a separate **Glide Travel**
calendar. Source appointments are never edited. Automation can be paused.

## 0:50–1:40 — Real run

Connect Google Calendar (test account, fictional names), enable Glide, run a
check. Show the source read, the real Amazon Location route, the Strands tool
sequence, and one block appearing in the travel calendar.

## 1:40–2:35 — Conflict and decision

Set the appointments so the next journey needs more time than exists. Show
the quantified shortfall and the **Needs your decision** card. Edit the
appointment, recheck, and show the block now fitting.

## 2:35–3:05 — Maintenance

Repeat the check: Activity shows `unchanged`, no duplicates. Move one
appointment again: the old block updates and the following journey
recalculates. Delete it: the obsolete block is removed.

## 3:05–3:35 — Architecture

Show `docs/architecture.png`: browser → CloudFront → API; EventBridge →
dispatcher → SQS FIFO → worker; DynamoDB, Bedrock, Amazon Location, Google.
One sentence each; keep SDK traces out of the everyday interface.

## 3:35–4:05 — Judge path

Open the labeled sample: "Sample calendar · simulated routes." Walk the
three-step judge flow and state that no Google account is needed.

## 4:05–4:30 — Close

Name, repository, live demo link, and roadmap: walking/transit, additional
calendars, and departure alerts after delivery.
