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
driving-time blocks in a separate **Glide Travel** calendar. It combines
route estimates with the user's arrival buffer and checks each journey
against the commitments around it.

When an appointment moves or disappears, Glide updates its blocks. When
travel cannot fit, it explains the shortfall and lets the person correct a
location, skip the journey, or edit the appointment and recheck. Users can
pause automation and stay in control of their original appointments.

The first version covers driving and one source calendar. A hosted sample
lets judges explore the workflow with fictional appointments and simulated
routes; the video demonstrates the real Google and AWS integrations.

## How we built it

The Strands Agents SDK connects calendar context, place lookup, route
estimation, and structured journey proposals through six typed tools.
Amazon Bedrock provides the model, and Amazon Location Service provides place
and driving-route information. Deterministic application code validates time
constraints and limits writes to Glide's own calendar events.

An AWS scheduler and FIFO queue keep the application checking while the
browser is closed. Persistent state connects appointments to their travel
blocks, so retries and schedule changes reconcile without duplicates, and
user-edited or deleted blocks are respected rather than overwritten.

## Accomplishments

[Replace with measured results: the live workflow, scenario outcomes,
retry/no-duplicate checks, and usability observations. Include sample sizes;
do not invent benefit claims.]

## Challenges we ran into

[Write the observed problems, the changes made, and the evidence that fixed
them. Candidates, only if they actually occurred: OAuth test-token expiry,
recurring-instance identity, ambiguous venues, estimates at the intended
time, duplicate prevention after timeouts.]

## What we learned

[Use real observations. Assess how much reliable behavior depended on the
tool boundaries and persistent state.]

## What's next for Glide

Extend the tested driving workflow to walking and public transport, add
per-journey preferences where coverage supports them, include additional
calendars, and improve departure alerts after delivery testing.

## Built With

`strands-agents-sdk`, `python`, `amazon-bedrock`, `amazon-location-service`,
`google-calendar-api`, `aws-lambda`, `amazon-eventbridge`, `amazon-sqs`,
`amazon-dynamodb`, `amazon-s3`, `amazon-cloudfront`, `amazon-api-gateway`,
`react`, `typescript`, `fastapi`, `aws-sam`.
