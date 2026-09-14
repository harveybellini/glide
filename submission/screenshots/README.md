# Gallery screenshots

Four recommended 3:2 screenshots, JPG/PNG, under 5 MB each (Devpost allows up
to 15). Regenerate them from the deployed sample
(https://d3tvxy281s2u11.cloudfront.net) so the gallery shows the build judges
meet; do not ship fixture state mislabeled as live.

| File | Shows |
| --- | --- |
| `01-landing.png` | Signed-out landing: tagline, Google connection status, Try a sample day |
| `02-timeline.png` | Day timeline with source events and the reserved travel block |
| `03-decision.png` | Needs-your-decision card with the quantified shortfall |
| `04-activity.png` | Activity receipts after a travel check |
| `05-mobile-welcome.png` | Full welcome page at 390 px width |
| `06-mobile-day.png` | Full daily workspace at 390 px width |
| `07-desktop-welcome-full.png` | Full desktop welcome page for design review |
| `08-desktop-day-full.png` | Full desktop daily workspace for design review |
| `09-guided-tour-welcome.png` | The guided tour on the landing page, spotlighting **Try a sample day** |
| `10-guided-tour-day.png` | The guided tour inside the day, spotlighting the reserved travel block |

Capture checklist:

- Use the labeled **Sample calendar · simulated routes** header so screenshots
  cannot be read as live provider results.
- 3:2 ratio; no personal data; no credentials; keyboard focus visible where
  relevant.

Current state (14 September 2026): all ten PNGs were retaken from the deployed
0.4.2 release (https://d3tvxy281s2u11.cloudfront.net, build `0676869`) and its
labelled `Sample calendar - simulated routes` workspace. The four gallery
shots (`01`–`04`), the guided-tour captures (`09`, `10`), and the full-page
desktop references (`07`, `08`) come from `frontend/e2e/screenshots.spec.ts`;
the full-page mobile references (`05`, `06`) come from the narrow-screen case
in `frontend/e2e/design.spec.ts`. From `frontend/`:

```powershell
$env:PLAYWRIGHT_BASE_URL = "https://d3tvxy281s2u11.cloudfront.net"
npm run screenshots
npx playwright test e2e/design.spec.ts -g "welcome and daily controls work on narrow screens"
```

Files `01`–`04` and `09`–`10` are 3:2 at 1200 x 800; `05`–`08` are full-page
design references. Every capture shows the sample label and the footer's version
badge, so the gallery cannot be read as live provider output.
