# Judge testing instructions

## Option A: hosted sample

The public site is https://d3tvxy281s2u11.cloudfront.net

1. Open the site in a fresh private window. No Google account is required. A
   guided tour opens on the first visit and spotlights each control in turn:
   **Skip the tour** or Escape leaves it, and **Show me around** brings it back.
2. Choose **Try a sample day**. The day is labeled **Sample calendar - simulated
   routes**; it uses fictional events and synthetic routes, not live providers.
   The sample starts watching immediately: the sage strip under the account bar
   says the agent will check every 15 minutes, and the first scheduled check
   runs on its own within a couple of minutes. Reload at any point; the strip
   reports the last check and the next one from the server, not from browser
   memory.
3. Leave the tab open or come back later: the page refreshes itself, and a
   **Needs your decision** card appears when the scheduled check finds the
   10-minute shortfall. The strip shows how many checks ran while you were
   away. (**Recheck now** is the same check on demand if you would rather not
   wait.)
4. Press **Edit appointments**, edit the 11:00 appointment to 10:45-11:15,
   save, and **Recheck now**. Expect two travel blocks and no open decision.
5. Press **Recheck now** again: no duplicate blocks (Activity shows
   `unchanged`).
6. After a fresh reset, let a scheduled check run (or press **Recheck now**)
   and try **Add it anyway** (optionally typing a note): the tight journey
   appears as a second travel block, and the decision stays answered on the
   next check. Also try **Skip this journey**, **Stop watching**, **Pause
   automation**, and the **Settings** arrival-buffer control.

## Option A2: connected Google account (owner test user only)

Requires the Google OAuth test account that the entry owner configured; no
judge account is needed.

> Status, 12 September: the live path works end to end for the owner's test
> account - real routes, marked `Travel - Glide` blocks in the primary
> calendar, and one decision email per shortfall. While the SES account is in
> the sandbox, that email only reaches verified recipients. Use Option A for
> the judge path, which needs no Google or AWS account.

1. Open the site and choose **Connect Google Calendar**. The OAuth flow returns
   to the same day view labeled **Your calendar - real routes**. Automation
   starts paused, so nothing runs against a real calendar until you say so.
2. Press **Start watching**: the status strip confirms the background cadence
   and Glide runs the first check straight away. In **Settings**, confirm a
   starting address through the real place search, the time zone, the arrival
   buffer, and the earliest departure time.
3. Travel blocks appear in the primary calendar as marked `Travel - Glide`
   events; ordinary appointments are never modified. The agent keeps checking
   in the background - close the tab to prove it - and the next visit's strip
   reports what ran while you were away.
4. Move an appointment in Google Calendar: the block updates or a decision
   appears instead of a guess, without pressing anything.
5. With notifications on in **Settings**, force a shortfall: one email arrives
   with a link that opens the highlighted card. A repeat check stays quiet,
   and the provider-free sample never sends mail.
6. **Stop watching** (or **Pause automation**), then **Disconnect**. Glide
   removes its own blocks, revokes the Google grant, and returns to the
   sample-only landing page.

## Option B: local sample

Requirements: Python 3.12+, `uv`, Node 20+.

```powershell
uv sync --frozen
uv run uvicorn glide.api.app:app --reload
# second terminal
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173` and follow Option A. Checks:

```powershell
uv run pytest
uv run ruff check .
cd frontend; npm run typecheck; npm run build
```

## What judges should not need

No personal Google account, no AWS account, no fees. The Google/OAuth flow
requires the owner's test account and is demonstrated in the video, not
required for sample evaluation.

## Known limits (to state honestly)

- Sample mode is synthetic; the live Amazon Location/Bedrock path and the real
  Google calendar writes are shown in the video.
- Only driving is supported in the MVP. Glide reads the primary calendar and
  writes only its own marked travel blocks; ordinary appointments are never
  modified.
- The dispatcher ticks every five minutes, so the first or next sample check
  can take up to about five minutes to appear even though the sample interval
  is 15. **Recheck now** is the honest escape hatch when a judge is watching.
