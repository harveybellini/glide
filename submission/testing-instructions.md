# Judge testing instructions

## Option A: hosted sample

The public site is https://d3tvxy281s2u11.cloudfront.net

1. Open the site in a fresh private window. No Google account is required.
2. Choose **Try a sample day**. The day is labeled **Sample calendar - simulated
   routes**; it uses fictional events and synthetic routes, not live providers.
3. Press **Recheck now**. Expect one blue travel block before the 11:00
   appointment and a **Needs your decision** card reporting a 10-minute
   shortfall.
4. Press **Edit appointments**, edit the 11:00 appointment to 10:45-11:15,
   save, and **Recheck now**. Expect two travel blocks and no open decision.
5. Press **Recheck now** again: no duplicate blocks (Activity shows
   `unchanged`).
6. Try **Skip this journey** after a fresh reset, **Pause automation**, and
   the **Settings** arrival-buffer control.

## Option A2: connected Google account (owner test user only)

Requires the Google OAuth test account that the entry owner configured; no
judge account is needed.

> Status, 11 September: the account connects and reads its calendar, but live
> planning currently fails before a proposal (`AgentProposalMissing`), so
> steps 3-4 do not complete yet. A prompt and turn-budget fix is in the
> working tree pending redeploy. Use Option A for a reliable judge path.

1. Open the site and choose **Connect Google Calendar**. The OAuth flow returns
   to the same day view labeled **Your calendar - real routes**.
2. In **Settings**, confirm a starting address through the real place search,
   the time zone, the arrival buffer, and the earliest departure time.
3. Choose **Resume automation**, then **Recheck now**. Travel blocks appear in
   the primary calendar as marked `Travel - Glide` events; ordinary
   appointments are never modified.
4. Move an appointment in Google Calendar and **Recheck now**: the block
   updates or a decision appears instead of a guess.
5. **Pause automation**, then **Disconnect**. Glide removes its own blocks,
   revokes the Google grant, and returns to the sample-only landing page.

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
