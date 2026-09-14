# Glide frontend style guide

Glide makes room for the journey between appointments. The interface should feel
calm, capable, and personal: warm paper, forest green, generous space, and clear
language. This guide is the reference for future frontend work.

## Design plan and implementation

1. Welcome: explain the benefit immediately, offer a no-account sample, and keep
   Google Calendar connection available. Show a clearly fictional calendar
   illustration, followed by three short explanations of the workflow.
2. Daily workspace: give the timeline the most space. Put navigation and automation
   controls in a persistent desktop rail; put Recheck now beside the page title.
3. Context: summarize appointments, travel blocks, arrival buffer, and decisions
   using actual API data. Separate ordinary appointments from Glide travel visually
   and with explicit text. Keep conflicts and their actions beside the plan.
4. Editing: retain inline appointment editing and an inline settings panel so the
   user can make changes without losing their place in the day.
5. Responsive behavior: collapse guidance beneath the timeline on tablets and
   convert the sidebar to a compact top navigation on phones. Preserve every action.
6. Verification: production build, existing sample workflow tests, responsive and
   error-state browser checks, then visual review of desktop and phone captures.
7. Guidance: a first-time visitor gets a guided tour that spotlights the real
   control for each step. It is never modal, it can be left at any point, and
   **Show me around** brings it back.

The implementation is in `frontend/src/App.tsx`, `components/Welcome.tsx`,
`components/Brand.tsx`, and `styles.css`; the tour is
`components/Tour.tsx` with its steps in `frontend/src/tour.ts`. Existing editor,
settings, connection, and API contracts remain the foundation. No new runtime
dependencies, remote fonts, or bitmap assets are needed. The illustration uses
HTML/CSS; the shared brand mark is an inline SVG.

## Palette

Use the CSS custom properties in `frontend/src/styles.css` as the source of truth.

| Token | Value | Purpose |
| --- | --- | --- |
| `--canvas` | `#f8f7f2` | Warm ivory page background |
| `--surface` | `#fffefa` | Appointments, forms, secondary buttons |
| `--ink` | `#263d34` | Main text and headings |
| `--muted` | `#687169` | Supporting text; never disabled by color alone |
| `--green` | `#2f5946` | Primary action, brand, travel accents |
| `--green-dark` | `#234534` | Primary hover and emphasized green text |
| `--sage` | `#e6ecdf` | Travel blocks and gentle highlights |
| `--line` | `#dce0d5` | Decorative separators and card boundaries |
| `--amber-bg` | `#faf0d9` | Decision background |
| `--amber-ink` | `#78541d` | Decision text |
| `--danger` | `#a12e2e` | Error text, paired with an error message |

Use dark text on light surfaces and warm white on primary green. Do not use the
decorative border token as the only indication of an input: inputs have a stronger
`#8a9783` boundary (3.04:1 against the input surface). Focus uses a visible
`#92642d` outline. Status dots always
have accompanying text. Recheck contrast when adding new color combinations.

## Typography and spacing

- Use the system sans-serif stack for controls, body copy, dates, and data.
- Use `--serif` (Georgia) for the main welcome heading, workspace title, and
  occasional editorial callouts. Italics add warmth to a short phrase.
- Welcome display type scales from 48 to 78 px; the workspace title scales from
  30 to 44 px. Keep operational headings compact and readable.
- Body copy is 12–18 px by context; primary controls are 13 px. Small metadata and
  decorative eyebrows are 10–12 px, with letter spacing for uppercase eyebrows.
  Do not put essential instructions in tiny decorative labels.
- Use tabular numerals for times and counts. Long locations must wrap.
- Use a roughly 4 px spacing rhythm: 8, 12, 16, 20, 24, 32, 40. Card padding is
  usually 20–25 px. Avoid compressing forms to match decorative examples.
- Cards use 10–16 px radii, buttons 8 px, inputs 6 px. Reserve the large arch and
  slight rotation for the welcome illustration. Operational cards stay level.

## Components and interaction

| Component | Contract |
| --- | --- |
| Brand | Reuse `Brand.tsx`; supply an accessible Glide heading in its parent. |
| Primary button | Forest green, descriptive verb, visible busy state, disabled while running. |
| Secondary button | Ivory surface, restrained outline, same focus treatment. |
| Navigation | Real anchors for page sections, buttons for actions; current destination has a background and text cue. |
| Appointment | Ivory card, start/end times, title, location, sample-only Edit control. |
| Travel block | Sage card with green marker, explicit `Travel · Glide` title, origin/destination, buffer, outcome. |
| Background status | Full-width sage strip under the account bar: pulse dot, what the agent is watching and its interval, last/next check, checks since the last visit, and the watching on/off control. Paused state uses the neutral surface. |
| Decision | Amber section, reason and quantified timing when available, only API-allowed actions. |
| Settings/editor | Labelled native fields, validation, Save and Cancel, Escape cancellation. |
| Activity | Operation, outcome, timestamp; show truthful empty and last-check states. |
| Guided tour | Transparent layer with a spotlight ring around the step's real element and a step card beside it; never modal, Escape ends it, arrow keys move between steps, focus moves into the card and returns to the opener, and a missing target renders nothing rather than an empty highlight. |
| Feedback | Errors use `role="alert"`; action outcomes use a polite live region. |

Display counts from the current day response. A zero count is not proof that a
check succeeded: before the first run, say the first background check is on its
way and offer Recheck now for an immediate one. Never invent success, travel
estimates, or connected accounts to fill empty space.

The current time helpers render and edit London wall time. The timeline explicitly
says “Times in London”; the settings timezone is a planning setting. Any future
timezone rendering change must update formatting and editing together and test
daylight-saving boundaries before changing that label.

Sample and live modes must remain identifiable. Sample appointments are editable
in the app; live appointments are edited in Google Calendar. Connection,
reconnection, disconnect, location search, pause/resume, reset, and decision
resolution must continue to use the existing API behavior.

## Responsive and accessibility rules

- Above 900 px: timeline and guidance sit alongside each other.
- At 900 px and below: guidance follows the timeline in the reading order.
- At 680 px and below: sidebar becomes top navigation; summary becomes two
  columns; guidance and activity become single-column; welcome becomes stacked.
- At 360 px and below: form fields stack. Verify down to 320 px without horizontal
  page scrolling and at 200% browser zoom.
- Keep the skip link first in keyboard order and its destination focusable.
- After Save, Cancel, or Escape closes an editor, return focus to its opener.
  Ignore Escape while saving, consistently with the disabled Cancel control.
- Preserve logical heading levels, navigation landmarks, accessible control names,
  visible focus, form labels, error announcements, and `aria-expanded` on toggles.
- Primary actions and inputs are at least 44 px tall. Compact desktop secondary
  controls may be 36 px; interactive elements must retain adequate separation.
- Never remove an action to make a small-screen layout fit.
- Respect `prefers-reduced-motion`: disable transitions and smooth scrolling.
- Decorative marks are hidden from assistive technology; meaningful status always
  includes words. The welcome example is explicitly identified as fictional.

## Voice

Be reassuring and specific. “Travel plan updated.” and “No decisions waiting.”
are good operational messages. “Room for the journey” belongs in brand moments.
Explain a problem and the next useful action without blame. Keep provider
credentials, database state, and implementation details out of user-facing copy.
Avoid claims that every journey is possible or that a check has succeeded before
the API confirms it.

## Handoff and verification

From `frontend`, run `npm run typecheck` and `npm run build`. With the local API
and frontend running as described in the root README, run:

```powershell
npm run e2e -- e2e/judge-path.spec.ts e2e/design.spec.ts
npm run screenshots
```

The judge-path suite covers conflict detection, editing, rechecking, duplicate
prevention, reset, settings, skip-link access, and persistent journey skipping.
The design suite covers narrow layouts, the 900/680/360 px and 200% zoom rules,
automation controls, recoverable loading errors, and connected empty-day
presentation. It also asserts this guide's contracts on every page and panel:
palette tokens, serif and sans typography, eyebrow sizes, 44 px controls, input
and card radii, focus colour, travel-block and decision treatments, and the
button contract for live-mode decision actions. Screenshots are review
artifacts, not pixel-diff assertions. Inspect the images as well as test results.

The build output is `frontend/dist`. This redesign does not require backend or
infrastructure changes. Deployment is a separate release operation; do not use
the full infrastructure deployment script merely to preview a UI change.
