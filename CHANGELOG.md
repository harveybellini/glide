# Changelog

Glide reads a calendar, works out the driving time between appointments, and
reserves that time in the same calendar. This file is the history behind the
version the page reports in its footer.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Every push adds an entry under `## [Unreleased]`; `scripts/version.py bump
minor|patch|major` releases those entries as a dated section and rewrites every
version declaration with them. See [AGENTS.md](AGENTS.md).

## [Unreleased]

<!-- Add a line here before you push. One sentence, in the user's words. -->

### Changed

- The repository is trimmed to the product and its submission artifacts;
  internal working notes are no longer part of the public tree.
- The Devpost story, AWS Builder article, and documentation cross-link only
  to what remains, and the claims match the deployed 0.4.2 build.

## [0.4.2] - 2026-09-14

### Fixed

- A freshly started sample day now gets its first background check inside one
  dispatcher tick, instead of waiting for the sweep to reach it past a table
  full of expired sessions.

## [0.4.1] - 2026-09-14

### Fixed

- A scheduled sample check is labelled as scheduled in the activity record,
  instead of reusing the on-demand sample label.

## [0.4.0] - 2026-09-12

### Added

- Glide now watches in the background and shows it: a sample day starts
  checking itself every 15 minutes, a connected calendar gets a Start
  watching control, and the day view reports the last check, the next one,
  and how many ran while the tab was closed.

### Changed

- The agent only runs while watching and due, with a per-tenant interval, a
  15-minute floor and three-session cap for anonymous samples, and no work
  after the sample's 24-hour lifetime, so the demo cannot run up a bill.
- The web page refreshes itself while open and on focus, so a decision the
  agent raised in the background is waiting without anyone pressing a button;
  Recheck now is now just an on-demand shortcut.
- The demo video now makes the decision email a required beat: the message
  arrives once, its link opens the highlighted card, and an unresolved repeat
  stays quiet.

## [0.3.1] - 2026-09-12

### Fixed

- The frontend CI job runs again: `src/api.ts` names the storage module with
  its extension, so `npm run verify:api-retry` can import the real UI module
  under Node's ESM resolver.
- Both open dependency advisories are cleared: `pytest` moves to 9.1.1, and
  the archived GitHub MCP server takes a patched `@modelcontextprotocol/sdk`
  through an override, with tests that fail if either one comes back.
  Dependabot now watches `uv.lock` and the three local MCP installs, which is
  why the pytest alert had gone unnoticed.

## [0.3.0] - 2026-09-12

### Added

- A guided tour for first-time visitors: the page spotlights the control to
  press, walks from the sample day through the first check, the reserved travel
  blocks, a decision, and the activity log, and **Show me around** replays it
  (or `?tour=1` forces it open).
- "Add it anyway" on a tight-journey decision: accept the shortfall, add an
  optional note, and Glide books the travel block so you arrive as the
  appointment starts.

### Fixed

- Deploying without an alarm address: `scripts/deploy-agent.ps1` no longer
  passes empty optional parameters through its splat array, and
  `scripts/deploy.ps1` leaves an unset address out of
  `sam deploy --parameter-overrides`, which rejects a blank value such as
  `AlarmEmail=` as an invalid format.

## [0.2.0] - 2026-09-12

### Added

- Version monitoring on the page: the footer names the version, commit, and
  build time the tab is running, compares them with the deployed `version.json`
  and the API's health response, and offers a reload when a newer deploy is
  live.
- A changelog gate: `scripts/version.py` keeps `VERSION`, `pyproject.toml`,
  `backend/glide/__init__.py`, `uv.lock`, `frontend/package.json`, and its
  lockfile in step, and blocks a push whose change set skips `CHANGELOG.md`
  (`scripts/hooks/pre-push`, CI, and the unit tests).

## [0.1.0] - 2026-09-11

The hackathon baseline, reconstructed from the commit history.

### Added

- The sample day, end to end: a fictional calendar with deterministic routes,
  inline editing, rechecks that stay idempotent, decisions to resolve, and a
  reset that returns the day to its starting state.
- The live Google Calendar path: OAuth sign-in, per-user settings, days, runs,
  and decisions, pause and resume, and a disconnect that revokes the grant and
  removes only the travel blocks Glide owns.
- Managed travel blocks: private, busy, green `Travel - Glide` events carrying
  private extension properties, conditional writes, respect for manual edits,
  deterministic event ids, and replay-safe reconciliation.
- Amazon Location routes and place search, plus a Strands/Bedrock agent runner
  with six typed planning tools, bounded turns, and deterministic validation.
- Decision notifications: at most one Amazon SES email per decision, stamped
  when the provider accepts it and linked back to the highlighted card.
- The deployed stack in `eu-west-1`: CloudFront and S3 for the interface,
  Lambdas for the API, worker, and dispatcher, SQS FIFO, and DynamoDB.
