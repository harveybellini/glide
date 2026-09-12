# Gallery screenshots

Four recommended 3:2 screenshots, JPG/PNG, under 5 MB each (Devpost allows up
to 15). Recapture from the deployed sample
(https://d3tvxy281s2u11.cloudfront.net) before submission; do not ship fixture
state mislabeled as live.

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

Current state (12 September 2026): the PNGs in this directory were
regenerated from the local sample app (label `Sample calendar -
simulated routes`) by `frontend/e2e/screenshots.spec.ts` and
`frontend/e2e/design.spec.ts`; `09` and `10` capture the guided tour a
first-time visitor meets. They are working references, not evidence of a
production deployment. Files `01–04` retain the 3:2 gallery format; `05–08` are
full-page design references. Recapture `01–04` against the final deployed release
before submission so every gallery screenshot shows the shipped experience.
