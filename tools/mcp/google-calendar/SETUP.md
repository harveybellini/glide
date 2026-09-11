# Google Calendar MCP

Inspection and testing surface for Glide's real-calendar work: verify created
travel blocks, their private markers, and disconnect cleanup.

The committed dependency is `@cocal/google-calendar-mcp` (the npm package of
[nspady/google-calendar-mcp](https://github.com/nspady/google-calendar-mcp)),
pinned to an exact version. It authenticates with an OAuth desktop client, so
no service-account key is needed and the granted scopes are visible in your
Google account's third-party access list.

## Install

```powershell
cd tools/mcp/google-calendar
npm install
```

## Google Cloud setup

1. Enable the Google Calendar API in a Google Cloud project.
2. Create an OAuth client ID of type **Desktop app** and download its JSON.
3. Save it as `tools/mcp/google-calendar/credentials.json`. The file is
   covered by the repo's `credentials*.json` gitignore rule; never commit it.
4. Add your own Google account as a test user on the OAuth consent screen.

The config sets `GOOGLE_OAUTH_CREDENTIALS` to that path. OAuth tokens are
stored by the server outside the repository (in your user profile).

## First run

1. Restart Codex and run `codex mcp list`; `google-calendar` should appear.
2. Ask the agent to authenticate with Google Calendar, then complete the
   browser flow once. Until you do, calendar tools fail with `-32600`.

## Verify

Ask the agent to list today's events on the test calendar. Because these tools
can mutate a real calendar, writes require approval
(`default_tools_approval_mode = "writes"`).
