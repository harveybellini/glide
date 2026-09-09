# Gallery screenshots

Four recommended 3:2 screenshots, JPG/PNG, under 5 MB each (Devpost allows up
to 15). Capture from the deployed sample once it exists; do not ship fixture
state mislabeled as live.

| File | Shows |
| --- | --- |
| `01-landing.png` | Signed-out landing: tagline, Google connection status, Try a sample day |
| `02-timeline.png` | Day timeline with source events and the reserved travel block |
| `03-decision.png` | Needs-your-decision card with the quantified shortfall |
| `04-activity.png` | Activity receipts after move/recheck, showing no duplicates |

Capture checklist:

- Use the labeled **Sample calendar · simulated routes** header so screenshots
  cannot be read as live provider results.
- 3:2 ratio; no personal data; no credentials; keyboard focus visible where
  relevant.

Current state (9 September 2026): the four PNGs in this directory were
regenerated from the local sample app (label `Sample calendar · simulated
routes`) by `frontend/e2e/screenshots.spec.ts`. They are kept as working
references. Recapture `01–04` against the final deployed release before
submission so every screenshot shows the shipped experience.
