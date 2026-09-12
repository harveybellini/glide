# Playwright MCP

Browser automation for Glide's React frontend: debug UI flows, walk through the
Google OAuth callback, and capture screenshots against the local API.

## Install

```powershell
cd tools/mcp/playwright
npm install
```

No browser download is needed: Chromium is already installed at
`%LOCALAPPDATA%\ms-playwright` (used by the frontend e2e tests) and is reused.

## Configured in Codex

See [mcp-section.toml](mcp-section.toml). The `launch.mjs` wrapper resolves the
package's CLI entry, so the config survives package upgrades.

## Verify

1. Restart Codex so it loads the new project config.
2. Run `codex mcp list` and confirm `playwright` appears.
3. In a session, ask the agent to open `http://localhost:5173` and take a
   screenshot. Optionally add `--browser chrome` to `args` in
   `.codex/config.toml` to drive your installed Chrome instead of headless
   Chromium.
