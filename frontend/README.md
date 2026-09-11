# Glide frontend

React, TypeScript, and Vite. Start the API using the root README, then run
`npm ci` and `npm run dev` in this directory. Vite proxies `/api` to port 8000.

The [frontend style guide](../docs/frontend-style-guide.md) contains the design
plan, tokens, typography, component contracts, responsive rules, accessibility
guidance, and copy guidelines for future changes.

```powershell
npm run typecheck
npm run build
npm run e2e -- e2e/judge-path.spec.ts e2e/design.spec.ts
npm run screenshots
```

The browser checks need a running frontend and API, plus an installed Playwright
Chromium browser. Set `PLAYWRIGHT_BASE_URL` to use a different local address.
Screenshots are written to `submission/screenshots` for manual review.

## September 2026 redesign verification

- Typecheck and production build pass.
- All eight Playwright scenarios pass against the production bundle and local
  sample API (11 September 2026). Coverage includes conflict resolution, repeat
  checks without duplicate travel, persistent journey skipping, settings,
  keyboard focus, automation controls, recoverable errors, and a mocked connected
  empty day. Layout checks cover 320, 390, 768, and 1440 px widths.
- Desktop and mobile screenshots were captured and visually reviewed. The local
  browser suite required permission to launch outside the Windows sandbox.
- The changes have not been deployed. The production artifact is `dist`.

For future changes, run the commands above in an environment that permits browser
processes and review desktop and mobile captures. Test against sample data;
connecting a real calendar is not required for the automated design scenarios.

Design references: [desktop welcome](../submission/screenshots/07-desktop-welcome-full.png),
[desktop day](../submission/screenshots/08-desktop-day-full.png),
[mobile welcome](../submission/screenshots/05-mobile-welcome.png), and
[mobile day](../submission/screenshots/06-mobile-day.png).
