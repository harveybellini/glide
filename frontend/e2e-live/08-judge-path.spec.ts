// The judge path end to end, exactly as the submission instructions describe
// it: open the demo, run a check, read the day, move an appointment, recheck,
// prove there are no duplicates, then reset.
import { test } from "@playwright/test";
import {
  Audit,
  LIVE_BASE,
  SESSION_KEY,
  expect,
  instrument,
  squeeze,
} from "./harness";

const audit = new Audit("08-judge-path");

test.afterAll(() => {
  audit.write();
});

type DayPayload = {
  date: string;
  label: string;
  source_events: {
    occurrence_id: string;
    title: string;
    start: string;
    end: string;
    location: string | null;
  }[];
  travel_blocks: {
    journey_key: string;
    start: string;
    end: string;
    padding_minutes: number | null;
    origin_occurrence_id: string | null;
    destination_occurrence_id: string | null;
  }[];
  decisions: { id: string; reason: string; allowed_actions: string[]; calculated_facts: Record<string, unknown> }[];
  last_run: { status: string; run_id: string; ended_at: string | null; safe_failure_code: string | null } | null;
};

async function readDay(page: import("@playwright/test").Page, sessionId: string) {
  const response = await page.request.get(`${LIVE_BASE}/api/day`, {
    headers: { "X-Glide-Session": sessionId },
  });
  expect(response.status(), "GET /api/day").toBe(200);
  return (await response.json()) as DayPayload;
}

async function readActivity(page: import("@playwright/test").Page, sessionId: string) {
  const response = await page.request.get(`${LIVE_BASE}/api/activity`, {
    headers: { "X-Glide-Session": sessionId },
  });
  expect(response.status(), "GET /api/activity").toBe(200);
  return (await response.json()) as {
    receipts: { id: string; operation: string; outcome: string; journey_key: string | null }[];
  };
}

// Clicks "Recheck now", captures the queued run id from the network, then
// waits for the API to report a terminal status. Returns the run id + status
// so the day payload can be tied to the run that produced it.
async function runCheck(
  page: import("@playwright/test").Page,
  sessionId: string,
  buttonLabel: RegExp,
) {
  const started = Date.now();
  const [queued] = await Promise.all([
    page.waitForResponse(
      (response) =>
        /\/api\/runs$/.test(response.url()) &&
        response.request().method() === "POST",
      { timeout: 30_000 },
    ),
    page.getByRole("button", { name: buttonLabel }).click(),
  ]);
  const { run_id: runId } = (await queued.json()) as { run_id: string };
  let status = "unknown";
  for (;;) {
    const poll = await page.request.get(`${LIVE_BASE}/api/runs/${runId}`, {
      headers: { "X-Glide-Session": sessionId },
    });
    if (poll.status() !== 200) {
      throw new Error(`GET /api/runs/${runId} -> ${poll.status()}`);
    }
    const payload = (await poll.json()) as { run: { status: string } };
    status = payload.run.status;
    if (["completed", "needs_input", "failed", "superseded", "paused"].includes(status)) {
      break;
    }
    if (Date.now() - started > 120_000) {
      throw new Error(`run ${runId} did not reach a terminal status (last ${status})`);
    }
    await page.waitForTimeout(250);
  }
  const durationMs = Date.now() - started;
  await expect(
    page.getByRole("button", { name: buttonLabel }),
    "Recheck button re-enables once the run is terminal",
  ).toBeEnabled({ timeout: 30_000 });
  return { runId, status, durationMs };
}

// The day payload carries last_run from a DynamoDB index, so poll it briefly
// instead of failing on a single eventual-consistency read.
async function waitForDayRun(
  page: import("@playwright/test").Page,
  sessionId: string,
  runId: string,
  timeoutMs = 20_000,
) {
  const started = Date.now();
  let last: DayPayload | null = null;
  for (;;) {
    last = await readDay(page, sessionId);
    if (last.last_run?.run_id === runId) {
      return { day: last, found: true, waitedMs: Date.now() - started };
    }
    if (Date.now() - started > timeoutMs) {
      return { day: last, found: false, waitedMs: Date.now() - started };
    }
    await page.waitForTimeout(500);
  }
}

function invariantTravelBlocks(day: DayPayload, auditArea: Audit, label: string) {
  const events = new Map(day.source_events.map((event) => [event.occurrence_id, event]));
  const keys = new Set<string>();
  for (const block of day.travel_blocks) {
    const start = Date.parse(block.start);
    const end = Date.parse(block.end);
    if (!(start < end)) {
      auditArea.high(`Travel block with a non-positive interval (${label})`, JSON.stringify(block));
    }
    if (keys.has(block.journey_key)) {
      auditArea.high(
        `Duplicate journey key rendered after ${label}`,
        `journey_key ${block.journey_key} appears more than once in /api/day`,
      );
    }
    keys.add(block.journey_key);

    const destination = block.destination_occurrence_id
      ? events.get(block.destination_occurrence_id)
      : undefined;
    if (destination) {
      const destStart = Date.parse(destination.start);
      if (Math.abs(destStart - end) > 60_000) {
        auditArea.medium(
          `Travel block does not finish at the destination start (${label})`,
          `block end ${block.end} vs destination start ${destination.start}`,
        );
      }
      const padding = (block.padding_minutes ?? 0) * 60_000;
      if (start >= destStart - padding) {
        auditArea.medium(
          `Travel block leaves no driving time before the buffer (${label})`,
          JSON.stringify(block),
        );
      }
    } else {
      auditArea.medium(`Travel block has no resolvable destination (${label})`, JSON.stringify(block));
    }

    for (const event of day.source_events) {
      const eventStart = Date.parse(event.start);
      const eventEnd = Date.parse(event.end);
      const overlaps = start < eventEnd && eventStart < end;
      if (overlaps) {
        auditArea.high(
          `Travel block overlaps a source appointment (${label})`,
          `${block.start}–${block.end} overlaps "${event.title}" ${event.start}–${event.end}`,
        );
      }
    }
  }
  return keys;
}

// /api/day returns UTC instants; the editor and the timeline work in the
// sample's own zone (Europe/London), so compare wall-clock time in that zone.
function londonWall(iso: string) {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(iso));
}

test("judge path: sample, check, conflict, replan, no duplicates, reset", async ({
  page,
}) => {
  const watch = instrument(page, audit);
  const checkDurations: number[] = [];

  // 1. Cold visitor lands on the marketing page with no stored session.
  await page.goto(LIVE_BASE, { waitUntil: "domcontentloaded" });
  await page.evaluate((key) => window.localStorage.removeItem(key), SESSION_KEY);
  await page.reload({ waitUntil: "networkidle" });
  await expect(page.getByRole("button", { name: /Try a sample day/i })).toBeVisible();
  audit.step("cold visit", "landing page rendered");
  await audit.shot(page, "01-landing");

  // 2. Start the sample day.
  const sampleStarted = Date.now();
  await page.getByRole("button", { name: /Try a sample day/i }).click();
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible({
    timeout: 30_000,
  });
  audit.step("start sample day", `${Date.now() - sampleStarted}ms to day view`);
  await expect(page.getByText("Sample workspace")).toBeVisible();

  const sessionId = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  expect(sessionId, "sample session id in localStorage").toBeTruthy();
  const initial = await readDay(page, sessionId as string);
  audit.dump("01-initial-day", initial);
  audit.step(
    "initial day",
    `${initial.source_events.length} events / ${initial.travel_blocks.length} travel blocks / ${initial.decisions.length} decisions`,
    initial.label,
  );
  expect(initial.source_events.length, "sample day appointments").toBeGreaterThanOrEqual(3);
  expect(initial.travel_blocks.length, "no travel blocks before the first check").toBe(0);
  await audit.shot(page, "02-sample-day-before-check");

  // 3. Ask Glide to plan the day.
  const firstRun = await runCheck(page, sessionId as string, /Recheck now/i);
  checkDurations.push(firstRun.durationMs);
  const firstDay = await waitForDayRun(page, sessionId as string, firstRun.runId);
  if (!firstDay.found) {
    audit.high(
      "A finished check never appears as last_run on /api/day",
      `Run ${firstRun.runId} reached "${firstRun.status}" but GET /api/day still reported ` +
        `last_run=${JSON.stringify(firstDay.day.last_run)} after ${firstDay.waitedMs}ms. ` +
        "The day view uses last_run for the \"Last check\" line and the CHECK COMPLETE quiet note, " +
        "so the judge sees \"READY WHEN YOU ARE\" after a successful check.",
    );
  }
  const afterCheck = firstDay.day;
  const afterCheckActivity = await readActivity(page, sessionId as string);
  audit.dump("02-after-first-check", { day: afterCheck, activity: afterCheckActivity });
  audit.step(
    "first check",
    `${firstRun.durationMs}ms`,
    `run ${firstRun.runId} ${firstRun.status}; ${afterCheck.travel_blocks.length} travel blocks, ${afterCheck.decisions.length} decisions, last_run ${afterCheck.last_run?.status ?? "null"} after ${firstDay.waitedMs}ms`,
  );
  expect(firstRun.status, "first run terminal status").toMatch(/completed|needs_input/);
  expect(
    afterCheck.travel_blocks.length + afterCheck.decisions.length,
    "the check must produce travel blocks, decisions, or both",
  ).toBeGreaterThan(0);
  invariantTravelBlocks(afterCheck, audit, "first check");
  await audit.shot(page, "03-after-first-check");

  // 4. Create (or confirm) the tight B→C journey by moving the middle stop.
  const middle = afterCheck.source_events[1];
  audit.step(
    "middle appointment",
    `${middle.title} ${middle.start}–${middle.end}`,
    middle.location ?? "no location",
  );
  const editButton = page
    .locator("article.row.event", { hasText: middle.title })
    .getByRole("button", { name: "Edit" });
  await editButton.click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible();
  await editor.getByLabel("Start").fill("10:45");
  await editor.getByLabel("End").fill("11:15");
  await editor.getByRole("button", { name: "Save changes" }).click();
  await expect(editor).toBeHidden({ timeout: 15_000 });
  audit.step("move middle appointment", "saved 10:45–11:15 through the UI");

  const movedDay = await readDay(page, sessionId as string);
  const movedEvent = movedDay.source_events.find(
    (event) => event.occurrence_id === middle.occurrence_id,
  );
  audit.dump("03-after-move", movedDay);
  expect(londonWall(movedEvent?.start as string), "moved appointment start (London)").toBe("10:45");
  expect(londonWall(movedEvent?.end as string), "moved appointment end (London)").toBe("11:15");

  // 5. Recheck after the source change.
  const secondRun = await runCheck(page, sessionId as string, /Recheck now/i);
  checkDurations.push(secondRun.durationMs);
  const afterReplan = (await waitForDayRun(page, sessionId as string, secondRun.runId)).day;
  audit.dump("04-after-replan", afterReplan);
  audit.step(
    "recheck after move",
    `${secondRun.durationMs}ms`,
    `run ${secondRun.runId} ${secondRun.status}; ${afterReplan.travel_blocks.length} travel blocks, ${afterReplan.decisions.length} decisions`,
  );
  invariantTravelBlocks(afterReplan, audit, "replan");
  await audit.shot(page, "04-after-replan");

  const firstBlockTimes = afterReplan.travel_blocks
    .map((block) => `${block.start}->${block.end}`)
    .sort()
    .join("|");
  const replanBlocks = afterReplan.travel_blocks.length;

  // 6. Repeat the same check: idempotency, no duplicate blocks.
  const thirdRun = await runCheck(page, sessionId as string, /Recheck now/i);
  checkDurations.push(thirdRun.durationMs);
  const afterRepeat = (await waitForDayRun(page, sessionId as string, thirdRun.runId)).day;
  audit.dump("05-after-repeat", afterRepeat);
  const repeatTimes = afterRepeat.travel_blocks
    .map((block) => `${block.start}->${block.end}`)
    .sort()
    .join("|");
  audit.step(
    "repeat check",
    `${thirdRun.durationMs}ms`,
    `run ${thirdRun.runId} ${thirdRun.status}; ${afterRepeat.travel_blocks.length} travel blocks (was ${replanBlocks})`,
  );
  expect(afterRepeat.travel_blocks.length, "no duplicate travel blocks on a repeat run").toBe(
    replanBlocks,
  );
  expect(repeatTimes, "repeat run keeps identical block times").toBe(firstBlockTimes);
  invariantTravelBlocks(afterRepeat, audit, "repeat");

  // 7. Resolve any outstanding decision through the UI, and record what was fixed.
  if (afterRepeat.decisions.length > 0) {
    const decision = afterRepeat.decisions[0];
    audit.step(
      "decision raised",
      decision.reason,
      JSON.stringify(decision.calculated_facts),
    );
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
    await audit.shot(page, "05-decision-card");
    const skip = page.getByRole("button", { name: /Skip this journey/i }).first();
    if (await skip.isVisible().catch(() => false)) {
      await skip.click();
      await expect(page.getByRole("button", { name: /Recheck now/i })).toBeEnabled({
        timeout: 120_000,
      });
      let resolved = await readDay(page, sessionId as string);
      const deadline = Date.now() + 20_000;
      while (
        resolved.decisions.some((candidate) => candidate.id === decision.id) &&
        Date.now() < deadline
      ) {
        await page.waitForTimeout(500);
        resolved = await readDay(page, sessionId as string);
      }
      audit.step("skip decision", "resolved", `${resolved.decisions.length} decisions left`);
      expect(
        resolved.decisions.some((candidate) => candidate.id === decision.id),
        "resolved decision is not raised again by the same run",
      ).toBe(false);
    } else {
      audit.medium(
        "Decision offered no visible action",
        `Decision ${decision.reason} rendered allowed actions ${JSON.stringify(decision.allowed_actions)} but none was clickable.`,
      );
    }
  } else {
    audit.step("decision step", "not applicable", "the plan resolved the day without a decision");
  }

  // 8. Reset returns the judge to the pristine sample.
  await page.getByRole("button", { name: /Reset sample/i }).click();
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeEnabled({
    timeout: 60_000,
  });
  const resetSessionId = await page.evaluate(
    (key) => window.localStorage.getItem(key),
    SESSION_KEY,
  );
  const afterReset = await readDay(page, resetSessionId as string);
  audit.dump("06-after-reset", afterReset);
  expect(afterReset.travel_blocks.length, "reset clears travel blocks").toBe(0);
  expect(afterReset.decisions.length, "reset clears decisions").toBe(0);
  expect(afterReset.source_events.length, "reset restores the sample appointments").toBe(
    initial.source_events.length,
  );
  const resetMiddle = afterReset.source_events[1];
  expect(resetMiddle.start, "reset restores the moved appointment").toBe(middle.start);
  await audit.shot(page, "06-after-reset");

  audit.step(
    "timings",
    checkDurations.map((value) => `${value}ms`).join(", "),
    "sample checks measured on the deployed site",
  );
  if (Math.max(...checkDurations) > 60_000) {
    audit.medium(
      "A sample check took over a minute",
      `Durations: ${checkDurations.join(", ")}ms. The documented sample target is under 60 seconds.`,
    );
  }

  // 9. Judge-facing copy and labels.
  const body = squeeze(await page.locator("body").innerText());
  for (const expected of [
    "Fictional events · Simulated routes",
    "Sample workspace",
    "Made for the space between.",
  ]) {
    if (!body.includes(expected)) {
      audit.medium(`Missing judge-facing label "${expected}"`, body.slice(0, 400));
    }
  }
  audit.step("judge labels", "checked", body.slice(0, 200));

  watch.assertClean("judge path");
});
