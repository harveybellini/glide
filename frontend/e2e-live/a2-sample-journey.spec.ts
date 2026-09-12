// A2: the documented judge path, end to end, on the deployed site.
// Verifies each expectation in submission/testing-instructions.md against the
// real API payloads, not just the rendered text.
import { test } from "@playwright/test";
import { Audit, expect, inventory, instrument, squeeze } from "./harness";
import { createSampleSession, fetchDay, openSample, queueRun, recheck, sleep } from "./orch-helpers";

const audit = new Audit("a2-sample-journey");

test.afterAll(() => {
  audit.write();
});

test("A2.1 a fresh sample day starts in the documented state", async ({ page, request }) => {
  const watch = instrument(page, audit);
  const session = await createSampleSession(request);
  const settingsResponse = await request.get("/api/me", { headers: { "X-Glide-Session": session } });
  const settings = (await settingsResponse.json()) as { padding_minutes: number; enabled: boolean };
  const day = await fetchDay(request, session);

  await openSample(page, session);
  audit.shot(page, "fresh-sample-day");

  audit.step(
    "fresh day payload",
    `events=${day.source_events.length} blocks=${day.travel_blocks.length} decisions=${day.decisions.length}`,
    JSON.stringify({ label: day.label, date: day.date, lastRun: day.last_run }),
  );
  expect(day.travel_blocks.length, "a fresh sample day has no travel blocks").toBe(0);
  expect(day.decisions.length, "a fresh sample day has no decisions").toBe(0);
  expect(day.source_events.length, "the sample day has appointments").toBeGreaterThan(0);

  await expect(page.getByText(day.label, { exact: false }).first()).toBeVisible();
  const bodyText = squeeze((await page.locator("main").innerText()) ?? "");
  if (!/sample/i.test(day.label)) {
    audit.high("Sample day is not labelled as a sample", `Deployed label: "${day.label}"`);
  } else {
    audit.ok("Day is labelled as the synthetic sample", day.label);
  }

  const stats = page.locator(".day-stats");
  const statsText = squeeze((await stats.innerText()) ?? "");
  const expectedAppointments = String(day.source_events.length).padStart(2, "0");
  if (!statsText.includes(expectedAppointments)) {
    audit.medium(
      "Appointment counter disagrees with the payload",
      `Stats read "${statsText}" but the day has ${day.source_events.length} appointments.`,
    );
  } else {
    audit.ok("Appointment counter matches the payload", `${expectedAppointments} appointments`);
  }
  if (!statsText.includes(String(settings.padding_minutes))) {
    audit.medium(
      "Arrival buffer shown in stats does not match settings",
      `Stats read "${statsText}" but settings.padding_minutes=${settings.padding_minutes}.`,
    );
  } else {
    audit.ok("Arrival buffer matches settings", `${settings.padding_minutes} min`);
  }

  if (!/READY WHEN YOU ARE|Let/i.test(bodyText)) {
    audit.step("quiet note copy", "not matched", bodyText.slice(0, 200));
  }
  const controls = await inventory(page);
  audit.dump("fresh-day-inventory", controls);
  audit.step("interactive controls on a fresh day", String(controls.length));

  watch.assertClean("fresh sample day");
  audit.dump("fresh-day-payload", day);
});

test("A2.2 Recheck now produces the documented block, decision and shortfall", async ({ page, request }) => {
  const watch = instrument(page, audit);
  const session = await createSampleSession(request);
  await openSample(page, session);

  await recheck(page);
  const day = await fetchDay(request, session);
  audit.dump("after-first-recheck", day);
  audit.shot(page, "after-first-recheck");

  if (day.travel_blocks.length !== 1) {
    audit.high(
      "First check did not produce exactly one travel block",
      `Expected 1 block, saw ${day.travel_blocks.length}.`,
      JSON.stringify(day.travel_blocks),
    );
  } else {
    audit.ok("First check produced one travel block");
  }
  if (day.decisions.length !== 1) {
    audit.high("First check did not produce exactly one decision", `Expected 1, saw ${day.decisions.length}.`);
  } else {
    audit.ok("First check produced one decision");
  }

  const decision = day.decisions[0];
  if (decision) {
    audit.step("decision reason", decision.reason, JSON.stringify(decision.calculated_facts));
    const facts = decision.calculated_facts as Record<string, number | undefined>;
    const shortfallMinutes = Math.ceil(Number(facts.shortfall_seconds ?? 0) / 60);
    const card = page.locator(`#decision-${decision.id}`);
    const cardText = squeeze((await card.innerText()) ?? "");
    audit.step("decision card text", cardText.slice(0, 300));
    if (!cardText.includes(`${shortfallMinutes} minutes`)) {
      audit.medium(
        "Decision card does not state the calculated shortfall",
        `calculated_facts imply a ${shortfallMinutes}-minute shortfall but the card reads: "${cardText}"`,
      );
    } else {
      audit.ok("Decision card states the calculated shortfall", `${shortfallMinutes} minutes`);
    }
    if (!/Skip this journey/i.test(cardText)) {
      audit.medium("Sample decision offers no skip control", cardText.slice(0, 300));
    }

    const destination = day.source_events.find((event) => event.occurrence_id === decision.occurrence_id);
    const block = day.travel_blocks[0];
    if (block && destination) {
      const blockEnd = new Date(block.end).getTime();
      const eventStart = new Date(destination.start).getTime();
      const paddingMs = (block.padding_minutes ?? 0) * 60_000;
      const deltaMinutes = (eventStart - blockEnd - paddingMs) / 60_000;
      audit.step(
        "travel block arithmetic",
        `block ${block.start}→${block.end}, destination ${destination.start}, padding ${block.padding_minutes}`,
        `destination - blockEnd - padding = ${deltaMinutes.toFixed(1)} min`,
      );
      if (Math.abs(deltaMinutes) > 1) {
        audit.high(
          "Travel block does not line up with the appointment and buffer",
          `Block ends ${block.end}, appointment starts ${destination.start}, padding ${block.padding_minutes} min — a gap of ${deltaMinutes.toFixed(1)} minutes.`,
        );
      } else {
        audit.ok("Travel block lines up with the appointment and buffer", `${deltaMinutes.toFixed(1)} min residual`);
      }
      const destinationTitle = destination.title;
      const timelineText = squeeze((await page.locator("#timeline").innerText()) ?? "");
      if (!timelineText.includes(destinationTitle)) {
        audit.medium("Destination title missing from the timeline", `Expected "${destinationTitle}"`);
      }
    }
  }

  const activityText = squeeze((await page.locator("#activity").innerText()) ?? "");
  audit.step("activity panel", activityText.slice(0, 300));
  if (!/Last check: (completed|needs_input)/.test(activityText)) {
    audit.medium("Activity panel does not report the last check", activityText.slice(0, 300));
  } else {
    audit.ok("Activity panel reports the last check");
  }

  const statusText = squeeze((await page.locator("p.status").innerText()) ?? "");
  audit.step("status message after recheck", statusText);
  if (!statusText) {
    audit.medium("Recheck now gives no status feedback", "The aria-live status region stayed empty.");
  }
  watch.assertClean("after first recheck");
});

test("A2.3 moving the appointment creates a second block and rechecks are idempotent", async ({ page, request }) => {
  const watch = instrument(page, audit);
  const session = await createSampleSession(request);
  await openSample(page, session);
  await recheck(page);

  const before = await fetchDay(request, session);
  const decision = before.decisions[0];
  expect(decision, "the first check should raise a decision").toBeTruthy();
  const targetEvent = before.source_events.find((event) => event.occurrence_id === decision.occurrence_id);
  expect(targetEvent, "the decision should point at an appointment").toBeTruthy();
  audit.step("appointment to move", `${targetEvent!.title} ${targetEvent!.start}→${targetEvent!.end}`);

  const row = page.locator("article.row.event").filter({ hasText: targetEvent!.title }).first();
  await expect(row).toBeVisible();
  await row.getByRole("button", { name: "Edit" }).click();

  const editor = page.getByRole("form", { name: /^Edit / });
  await expect(editor).toBeVisible();
  await expect(editor.getByLabel("Start")).toHaveValue(/^\d{2}:\d{2}$/);
  const prefill = {
    start: await editor.getByLabel("Start").inputValue(),
    end: await editor.getByLabel("End").inputValue(),
    location: await editor.getByLabel("Location").inputValue(),
  };
  audit.step("editor prefill", JSON.stringify(prefill));

  await editor.getByLabel("Start").fill("10:45");
  await editor.getByLabel("End").fill("11:15");
  const patch = page.waitForResponse(
    (response) => response.url().includes("/api/demo/events/") && response.request().method() === "PATCH",
  );
  await editor.getByRole("button", { name: /Save changes/i }).click();
  const patchResponse = await patch;
  audit.step("PATCH event", String(patchResponse.status()), (await patchResponse.text()).slice(0, 200));
  await expect(editor).toBeHidden({ timeout: 15_000 });

  const moved = await fetchDay(request, session);
  const movedEvent = moved.source_events.find((event) => event.occurrence_id === decision.occurrence_id);
  audit.step("moved appointment", `${movedEvent?.start}→${movedEvent?.end}`);

  await recheck(page);
  const after = await fetchDay(request, session);
  audit.dump("after-move-recheck", after);
  audit.shot(page, "after-move-recheck");
  if (after.travel_blocks.length === 2 && after.decisions.length === 0) {
    audit.ok("Moving the appointment yields two blocks and no open decision");
  } else {
    audit.high(
      "The documented move did not yield two blocks with no decision",
      `blocks=${after.travel_blocks.length}, decisions=${after.decisions.length}`,
      JSON.stringify({ blocks: after.travel_blocks, decisions: after.decisions.map((d) => d.reason) }),
    );
  }

  await recheck(page);
  const idempotent = await fetchDay(request, session);
  const activityText = squeeze((await page.locator("#activity").innerText()) ?? "");
  audit.step("activity after the repeat check", activityText.slice(0, 400));
  audit.shot(page, "after-idempotent-recheck");
  if (idempotent.travel_blocks.length !== 2) {
    audit.high(
      "A repeat check duplicated or removed travel blocks",
      `blocks=${idempotent.travel_blocks.length} after a no-op recheck`,
    );
  } else {
    audit.ok("A repeat check kept exactly two blocks");
  }
  if (!/unchanged/i.test(activityText)) {
    audit.medium(
      "Repeat check does not report an unchanged outcome",
      `Activity reads: "${activityText.slice(0, 300)}". The documented expectation is an "unchanged" receipt.`,
    );
  } else {
    audit.ok("Repeat check reports an unchanged outcome");
  }
  watch.assertClean("after move and repeat check");
});

test("A2.4 reset, skip and pause/resume behave as documented", async ({ page, request }) => {
  const watch = instrument(page, audit);
  const session = await createSampleSession(request);
  await openSample(page, session);
  await recheck(page);
  let day = await fetchDay(request, session);
  expect(day.decisions.length, "a decision is needed for this test").toBeGreaterThan(0);

  // Reset returns the day to its starting state.
  await page.getByRole("button", { name: /Reset sample/i }).click();
  await expect(page.getByText(/Sample reset to its starting state/i)).toBeVisible({ timeout: 30_000 });
  day = await fetchDay(request, session);
  audit.step("after reset", `blocks=${day.travel_blocks.length} decisions=${day.decisions.length}`);
  if (day.travel_blocks.length || day.decisions.length) {
    audit.high(
      "Reset did not return the day to its starting state",
      `blocks=${day.travel_blocks.length}, decisions=${day.decisions.length}`,
    );
  } else {
    audit.ok("Reset restores the fresh sample day");
  }

  // Recheck, then skip the journey.
  await recheck(page);
  day = await fetchDay(request, session);
  expect(day.decisions.length, "skip test needs a decision").toBeGreaterThan(0);
  const decisionId = day.decisions[0].id;
  await page.locator(`#decision-${decisionId}`).getByRole("button", { name: /Skip this journey/i }).click();
  await expect(page.getByText(/Journey skipped/i)).toBeVisible({ timeout: 60_000 });
  await sleep(500);
  day = await fetchDay(request, session);
  audit.shot(page, "after-skip");
  if (day.decisions.length === 0) {
    audit.ok("Skip closes the decision");
  } else {
    audit.high("Skip did not close the decision", JSON.stringify(day.decisions.map((d) => d.id)));
  }
  const skipActivity = squeeze((await page.locator("#activity").innerText()) ?? "");
  audit.step("activity after skip", skipActivity.slice(0, 400));

  // The skipped journey must not come back on the next check.
  await recheck(page);
  day = await fetchDay(request, session);
  if (day.decisions.length === 0) {
    audit.ok("A skipped journey is not re-raised by the next check");
  } else {
    audit.high(
      "A skipped journey came back on the next check",
      `decision reason ${day.decisions[0].reason}; the user's skip was not respected.`,
    );
  }

  // Pause / resume.
  await page.getByRole("button", { name: /Pause automation/i }).click();
  await expect(page.getByText(/^Glide is paused$/)).toBeVisible({ timeout: 20_000 });
  const pausedSettings = (await (await request.get("/api/me", { headers: { "X-Glide-Session": session } })).json()) as {
    enabled: boolean;
  };
  audit.shot(page, "paused");
  if (pausedSettings.enabled === false) {
    audit.ok("Pause automation persists", "settings.enabled = false");
  } else {
    audit.high("Pause automation did not persist", JSON.stringify(pausedSettings));
  }

  const queuedWhilePaused = await queueRun(request, session);
  audit.step(
    "run queued while paused",
    `status ${queuedWhilePaused.status}`,
    queuedWhilePaused.body.slice(0, 160),
  );
  if (queuedWhilePaused.runId) {
    let runStatus = "";
    for (let attempt = 0; attempt < 40; attempt += 1) {
      const poll = await request.get(`/api/runs/${queuedWhilePaused.runId}`, {
        headers: { "X-Glide-Session": session },
      });
      runStatus = ((await poll.json()) as { run: { status: string } }).run.status;
      if (["completed", "needs_input", "failed", "superseded", "paused"].includes(runStatus)) break;
      await sleep(500);
    }
    audit.step("run status while paused", runStatus);
    const afterPausedRun = await fetchDay(request, session);
    if (afterPausedRun.travel_blocks.length > day.travel_blocks.length) {
      audit.high(
        "A paused tenant still gained travel blocks",
        `blocks went ${day.travel_blocks.length} -> ${afterPausedRun.travel_blocks.length} while automation was paused.`,
      );
    } else {
      audit.ok("Paused automation wrote nothing", `run status ${runStatus}`);
    }
  }

  await page.getByRole("button", { name: /Resume automation/i }).click();
  await expect(page.getByText(/^Glide is on$/)).toBeVisible({ timeout: 20_000 });
  const resumed = (await (await request.get("/api/me", { headers: { "X-Glide-Session": session } })).json()) as {
    enabled: boolean;
  };
  if (resumed.enabled === true) {
    audit.ok("Resume automation persists", "settings.enabled = true");
  } else {
    audit.high("Resume automation did not persist", JSON.stringify(resumed));
  }
  watch.assertClean("reset/skip/pause journey");
});
