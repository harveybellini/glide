// Focused probe for one defect: /api/day never reports the finished run, so
// the day view can never show "Last check: ..." or the CHECK COMPLETE note.
import { test } from "@playwright/test";
import { Audit, LIVE_BASE, SESSION_KEY, expect, instrument, squeeze } from "./harness";

const audit = new Audit("10-lastrun-ui");

test.afterAll(() => {
  audit.write();
});

test("a finished check is invisible in the day view's run status", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.goto(LIVE_BASE, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Try a sample day/i }).click();
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible({
    timeout: 30_000,
  });
  const sessionId = (await page.evaluate(
    (key) => window.localStorage.getItem(key),
    SESSION_KEY,
  )) as string;

  const [queued] = await Promise.all([
    page.waitForResponse(
      (response) =>
        /\/api\/runs$/.test(response.url()) && response.request().method() === "POST",
    ),
    page.getByRole("button", { name: /Recheck now/i }).click(),
  ]);
  const { run_id: runId } = (await queued.json()) as { run_id: string };

  let runStatus = "";
  const started = Date.now();
  for (;;) {
    const poll = await page.request.get(`${LIVE_BASE}/api/runs/${runId}`, {
      headers: { "X-Glide-Session": sessionId },
    });
    const payload = (await poll.json()) as { run: { status: string } };
    runStatus = payload.run.status;
    if (["completed", "needs_input", "failed", "superseded", "paused"].includes(runStatus)) break;
    if (Date.now() - started > 60_000) throw new Error("run never finished");
    await page.waitForTimeout(200);
  }

  // Wait for the UI to finish its own polling round and re-render.
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeEnabled({
    timeout: 60_000,
  });
  await page.waitForTimeout(2_000);

  const day = (await (
    await page.request.get(`${LIVE_BASE}/api/day`, {
      headers: { "X-Glide-Session": sessionId },
    })
  ).json()) as { last_run: unknown; travel_blocks: unknown[] };
  const body = squeeze(await page.locator("body").innerText());
  audit.dump("lastrun-probe", {
    runId,
    runStatus,
    dayLastRun: day.last_run,
    travelBlocks: day.travel_blocks.length,
    showsLastCheckLine: body.includes("Last check:"),
    showsCheckComplete: body.includes("CHECK COMPLETE"),
    showsReadyWhenYouAre: body.includes("READY WHEN YOU ARE"),
  });
  audit.step(
    "run finished",
    `${runId} ${runStatus}`,
    `GET /api/day last_run=${JSON.stringify(day.last_run)}`,
  );

  // The run is terminal, so the day view has everything it needs to show them.
  if (day.last_run === null && runStatus !== "failed") {
    audit.high(
      "The day view can never show its run status",
      `Run ${runId} finished as "${runStatus}" and created ${day.travel_blocks.length} travel block(s), ` +
        `but GET /api/day still returns last_run=null (DynamoDB adapter bug: _run_item stores sk="RUN" ` +
        `while get_latest_run filters on sk.startswith("RUN#")). The UI therefore renders no "Last check:" ` +
        `line and shows "READY WHEN YOU ARE / Let's connect the dots." instead of the CHECK COMPLETE state ` +
        `after a successful check.`,
      JSON.stringify({
        observedsymptom: {
          showsLastCheckLine: body.includes("Last check:"),
          showsCheckComplete: body.includes("CHECK COMPLETE"),
          showsReadyWhenYouAre: body.includes("READY WHEN YOU ARE"),
        },
      }),
    );
  }
  await audit.shot(page, "after-finished-check");
  watch.assertClean("last-run probe");
});

test("a completed check leaves the quiet note on READY WHEN YOU ARE", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.goto(LIVE_BASE, { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Try a sample day/i }).click();
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible({
    timeout: 30_000,
  });
  const sessionId = (await page.evaluate(
    (key) => window.localStorage.getItem(key),
    SESSION_KEY,
  )) as string;

  // Move the middle appointment later so the tight journey fits and the run
  // finishes "completed" with no decision card.
  const editButton = page
    .locator("article.row.event", { hasText: "Appointment" })
    .getByRole("button", { name: "Edit" });
  await editButton.click();
  const editor = page.locator("form.event-editor");
  await editor.getByLabel("Start").fill("10:45");
  await editor.getByLabel("End").fill("11:15");
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(editor).toBeHidden({ timeout: 15_000 });

  const [queued] = await Promise.all([
    page.waitForResponse(
      (response) =>
        /\/api\/runs$/.test(response.url()) && response.request().method() === "POST",
    ),
    page.getByRole("button", { name: /Recheck now/i }).click(),
  ]);
  const { run_id: runId } = (await queued.json()) as { run_id: string };
  let runStatus = "";
  const started = Date.now();
  for (;;) {
    const poll = await page.request.get(`${LIVE_BASE}/api/runs/${runId}`, {
      headers: { "X-Glide-Session": sessionId },
    });
    runStatus = ((await poll.json()) as { run: { status: string } }).run.status;
    if (["completed", "needs_input", "failed", "superseded", "paused"].includes(runStatus)) break;
    if (Date.now() - started > 60_000) throw new Error("run never finished");
    await page.waitForTimeout(200);
  }
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeEnabled({
    timeout: 60_000,
  });
  await page.waitForTimeout(2_000);

  const day = (await (
    await page.request.get(`${LIVE_BASE}/api/day`, {
      headers: { "X-Glide-Session": sessionId },
    })
  ).json()) as { last_run: unknown; decisions: unknown[]; travel_blocks: unknown[] };
  const body = squeeze(await page.locator("body").innerText());
  const observed = {
    runId,
    runStatus,
    dayLastRun: day.last_run,
    decisions: day.decisions.length,
    travelBlocks: day.travel_blocks.length,
    showsLastCheckLine: body.includes("Last check:"),
    showsCheckComplete: body.includes("CHECK COMPLETE"),
    showsReadyWhenYouAre: body.includes("READY WHEN YOU ARE"),
  };
  audit.dump("lastrun-probe-completed", observed);
  audit.step("completed run", `${runId} ${runStatus}`, JSON.stringify(observed));
  if (runStatus === "completed" && observed.showsReadyWhenYouAre && !observed.showsCheckComplete) {
    audit.high(
      "A completed check still says \"READY WHEN YOU ARE\"",
      `Run ${runId} completed with ${day.travel_blocks.length} travel blocks and no decisions, ` +
        "so the quiet note should read CHECK COMPLETE / \"No decisions waiting.\". It instead shows " +
        "READY WHEN YOU ARE / \"Let's connect the dots.\" because last_run is never returned, and the " +
        "expected \"Last check: completed at HH:MM\" line is missing entirely.",
      JSON.stringify(observed),
    );
  } else if (runStatus !== "completed") {
    audit.step("completed variant not reachable", runStatus, JSON.stringify(observed));
  }
  await audit.shot(page, "completed-run-state");
  watch.assertClean("completed run probe");
});
