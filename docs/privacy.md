# Privacy and data handling

## What Glide reads

- The user's primary Google Calendar for a bounded rolling window
  (`singleEvents=true`, paginated). Only title, location, times, status,
  transparency, self attendance, and recurrence identity are used.
- Amazon Location is queried with location text and returns candidate places
  and driving-time estimates.

## What Glide writes

- Private busy events named "Travel · Glide" in the user's primary calendar.
  Events carry no attendees, conferencing, or reminders, and store
  Glide's journey key and applied hash in private extended properties so they are recognized as app-owned and excluded from source planning.
- Source appointments are never modified.

## What is stored

- Server-side sessions (encrypted cookie) and per-user OAuth tokens in
  Secrets Manager.
- A minimal active snapshot of source events for the current window
  (targeted at 48 hours), managed blocks, plans, decisions, and redacted
  receipts. Receipts expire after seven days; synthetic sample tenants expire
  after 24 hours.
- One optional notification address per signed-in user, defaulted to the
  Google account address at sign-in. It is used only to send the
  "needs your decision" email, can be paused or cleared in Settings, and is
  never included in logs or sent to any provider other than Amazon SES. The
  email body names the calculated shortfall and links back to the app; it does
  not repeat calendar titles or locations.
- Attendees, attachments, tokens, and full descriptions are omitted by
  default. Event text is treated as untrusted data by the agent, never as
  instructions.

## What is not done

- No analytics trackers, no sale or sharing of calendar data, and no
  notification channel other than the transactional email above. A Slack
  notification adapter is planned future work; it would store a per-user
  Slack identity instead of an address and follow the same once-only rule.
  The OAuth scopes are `openid`, `email`, and
  `calendar.events.owned` (event-level read/write on calendars the user owns; the application restricts itself to its own marked events).
- The remaining race between the source re-read and a conditional block write
  is documented in the reconciliation section of `plan.md`.

## Logs

Logs carry tool names, durations, safe reason codes, hashes, and usage
counters. They never contain tokens, attendee details, raw model traces, the
notification address, or email content; a failed send logs the decision id and
the error type only.
