import {
  Audit,
  apiGet,
  createSession,
  expect,
  instrument,
  inventory,
  LIVE_BASE,
  openWithSession,
  squeeze,
} from "./harness";
import { test } from "@playwright/test";

// Area: controls — the control surface of the day view (settings form,
// automation toggle, reset, decision actions) driven the way a user drives it.
// Focus: bounds/validation reachability, what does and does not persist, and
// whether the visible status text matches what the server actually did.

const audit = new Audit("controls");

test.afterAll(() => {
  const payload = audit.write();
  console.log("CONTROLS_COUNTS", JSON.stringify(payload.counts));
});

interface Settings {
  padding_minutes: number;
  earliest_departure?: string | null;
  time_zone: string;
  enabled: boolean;
  start_place?: { id?: string; label?: string } | null;
}

interface Day {
  travel_blocks: unknown[];
  decisions: { id: string }[];
  source_events: unknown[];
  last_run?: { status?: string | null } | null;
}

async function openDay(page: import("@playwright/test").Page, session: string) {
  await openWithSession(page, session);
  try {
    await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible({
      timeout: 30_000,
    });
    return true;
  } catch {
    audit.blocker(
      "Sample session did not open the day view",
      "A session created through POST /api/demo/session then seeded into localStorage never reached the timeline, so no control-surface checks could run.",
      squeeze(await page.locator("body").innerText().catch(() => "no body")),
    );
    return false;
  }
}

async function openSettings(page: import("@playwright/test").Page) {
  const button = page.getByRole("button", { name: "Settings", exact: true });
  const panel = page.getByRole("form", { name: "Travel settings" });
  try {
    await button.click();
    await expect(panel).toBeVisible({ timeout: 15_000 });
    return panel;
  } catch {
    audit.high(
      "Settings panel did not open",
      "Clicking the Settings button did not reveal the Travel settings form.",
      squeeze(await page.locator("body").innerText().catch(() => "no body")),
    );
    return null;
  }
}

async function clickSave(page: import("@playwright/test").Page, panel: import("@playwright/test").Locator) {
  await panel.getByRole("button", { name: /save settings/i }).click();
}

// Success signal: the panel unmounts only from onSaved(). The status line is
// not usable as a signal because "Settings saved." stays on screen.
async function saveOutcome(page: import("@playwright/test").Page, panel: import("@playwright/test").Locator) {
  try {
    await expect(panel).toHaveCount(0, { timeout: 3_000 });
    return { closed: true, status: squeeze(await page.locator("p.status").innerText()) };
  } catch {
    return { closed: false, status: squeeze(await page.locator("p.status").innerText().catch(() => "")) };
  }
}

test("arrival buffer bounds: what the user sees, and what actually persists", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  if (!(await openDay(page, session))) return;

  const baseline = await apiGet<Settings>("/api/me", session);
  audit.step("GET /api/me before", `padding_minutes=${baseline.padding_minutes}`);

  for (const attempt of ["61", "99", "60", "0"]) {
    const observedBefore = (await apiGet<Settings>("/api/me", session)).padding_minutes;
    const panel = await openSettings(page);
    if (!panel) return;
    const input = panel.getByLabel("Arrival buffer (minutes)");
    await input.fill(attempt);
    await clickSave(page, panel);
    const outcome = await saveOutcome(page, panel);
    const attached = (await input.count()) > 0;
    const dom = attached
      ? await input
          .evaluate((element: HTMLInputElement) => ({
            value: element.value,
            valid: element.validity.valid,
            rangeOverflow: element.validity.rangeOverflow,
            validationMessage: element.validationMessage,
            ariaInvalid: element.getAttribute("aria-invalid"),
          }))
          .catch(() => null)
      : null;
    const panelAttached = (await panel.count()) > 0;
    const inlineAlerts = panelAttached
      ? await panel
          .locator('[role="alert"]')
          .allInnerTexts()
          .catch(() => [] as string[])
      : [];
    const after = await apiGet<Settings>("/api/me", session);
    const inRange = ["0", "60"].includes(attempt);
    const evidence = JSON.stringify(
      {
        typed: attempt,
        observedBefore,
        observedAfter: after.padding_minutes,
        persisted: after.padding_minutes !== observedBefore,
        dom,
        inlineAlerts,
        panelClosed: outcome.closed,
        statusText: outcome.status,
      },
      null,
      2,
    );
    audit.step(
      `buffer=${attempt} submit`,
      outcome.closed ? "panel closed (saved)" : "panel stayed open (submit blocked)",
      evidence,
    );
    if (inRange && after.padding_minutes !== Number(attempt)) {
      audit.high(
        `Arrival buffer ${attempt} did not persist`,
        `GET /api/me returned padding_minutes=${after.padding_minutes} after saving ${attempt}.`,
        evidence,
      );
    } else if (!inRange && after.padding_minutes !== observedBefore) {
      audit.high(
        `Out-of-range arrival buffer persisted (${attempt})`,
        `Typing ${attempt} into Arrival buffer changed the stored value from ${observedBefore} to ${after.padding_minutes}, outside the documented 0-60 range.`,
        evidence,
      );
    } else if (!inRange) {
      audit.ok(
        `Arrival buffer ${attempt} is rejected, not persisted`,
        `Server value stayed ${observedBefore}; submit blocked, browser message "${dom?.validationMessage ?? ""}".`,
      );
    } else {
      audit.ok(
        `Arrival buffer ${attempt} saves and persists`,
        `GET /api/me padding_minutes=${after.padding_minutes}; stats read "${squeeze(await page.locator(".day-stats").textContent().catch(() => ""))}".`,
      );
    }
    if (!outcome.closed && inlineAlerts.length === 0 && dom?.validationMessage) {
      audit.low(
        `Out-of-range buffer ${attempt} only gets a native browser bubble`,
        "The app ships its own 0-60 message but the form never reaches it: the number input has min/max, so constraint validation blocks submit before onSubmit runs. No element with role=alert and no aria-invalid appears in the DOM, so the only feedback is the browser's transient tooltip.",
        evidence,
      );
    }
    if (!outcome.closed) {
      await page.keyboard.press("Escape");
      if (await panel.isVisible().catch(() => false)) {
        await panel.getByRole("button", { name: "Cancel", exact: true }).click().catch(() => {});
      }
      if (await panel.isVisible().catch(() => false)) {
        audit.medium(
          "Settings panel could not be dismissed after a blocked save",
          "Neither Escape nor Cancel closed the panel, so the only way out is reloading the page.",
          JSON.stringify({ typed: attempt }),
        );
      }
    }
    if ((await panel.count()) > 0) {
      audit.medium(
        "Settings panel stayed open after Escape/Cancel",
        "The panel could not be dismissed by keyboard or by its Cancel button.",
        JSON.stringify({ typed: attempt }),
      );
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle").catch(() => {});
    }
  }

  watch.assertClean("buffer bounds");
  await audit.shot(page, "buffer-bounds-after-save");
});

test("earliest departure round-trip, and what clearing it actually does", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  if (!(await openDay(page, session))) return;

  let panel = await openSettings(page);
  if (!panel) return;
  await panel.getByLabel("Earliest departure (optional)").fill("07:45");
  await clickSave(page, panel);
  const setOutcome = await saveOutcome(page, panel);
  const afterSet = await apiGet<Settings>("/api/me", session);
  audit.step(
    "set earliest departure",
    `saved=${setOutcome.closed}`,
    `API earliest_departure=${JSON.stringify(afterSet.earliest_departure)}`,
  );

  panel = await openSettings(page);
  if (!panel) return;
  const input = panel.getByLabel("Earliest departure (optional)");
  const roundTrip = await input.inputValue();
  audit.step(
    "reopen shows",
    JSON.stringify(roundTrip),
    `API returned ${JSON.stringify(afterSet.earliest_departure)}`,
  );
  if (roundTrip !== "07:45") {
    audit.low(
      "Earliest departure round-trips with seconds",
      'GET /api/me returns "07:45:00" and the panel passes it straight to <input type="time">, so the DOM value is "07:45:00" instead of the "07:45" a user would type. The field still renders 07:45, so this is invisible unless something compares the value.',
      JSON.stringify({ domValue: roundTrip, apiValue: afterSet.earliest_departure }),
    );
  } else {
    audit.ok("Earliest departure round-trips cleanly", "form value matches the saved time");
  }

  await input.fill("");
  const clearedValue = await input.inputValue();
  await clickSave(page, panel);
  const clearOutcome = await saveOutcome(page, panel);
  const afterClear = await apiGet<Settings>("/api/me", session);
  const reopened = await openSettings(page);
  if (!reopened) return;
  const reopenedValue = await reopened.getByLabel("Earliest departure (optional)").inputValue();
  const shot = await audit.shot(page, "departure-after-clearing");
  const evidence = JSON.stringify(
    {
      formValueAfterClearing: clearedValue,
      panelClosed: clearOutcome.closed,
      statusText: clearOutcome.status,
      apiAfterClear: afterClear.earliest_departure ?? null,
      valueAfterReopen: reopenedValue,
      screenshot: shot,
    },
    null,
    2,
  );
  audit.step("clear earliest departure", `valueAfterReopen=${JSON.stringify(reopenedValue)}`, evidence);
  if (afterClear.earliest_departure) {
    audit.medium(
      "Earliest departure cannot be cleared once set",
      "Emptying the field and pressing Save reports success, but the old time comes straight back: the panel never sends earliest_departure when the field is empty, and PATCH /api/settings only applies it when the JSON value is non-null. The user is told the setting was saved while nothing changed.",
      evidence,
    );
  } else {
    audit.ok("Earliest departure can be cleared", "API returned no earliest_departure after clearing.");
  }

  watch.assertClean("earliest departure");
});

test("paused automation and Recheck now: does the status text match the server", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  if (!(await openDay(page, session))) return;

  await page.getByRole("button", { name: "Pause automation" }).click();
  const pausedVisible = await page
    .getByText("Glide is paused")
    .isVisible()
    .catch(() => false);
  const paused = await apiGet<Settings>("/api/me", session);
  const dayBefore = await apiGet<Day>("/api/day", session);
  audit.step(
    "pause automation",
    `enabled=${paused.enabled}, "Glide is paused" visible=${pausedVisible}`,
    `travel_blocks=${dayBefore.travel_blocks.length} decisions=${dayBefore.decisions.length}`,
  );

  await page.getByRole("button", { name: /recheck now/i }).click();
  await page.waitForFunction(
    () => {
      const node = document.querySelector("p.status");
      const text = node?.textContent?.trim() ?? "";
      return text.length > 0 && !/planning travel/i.test(text);
    },
    undefined,
    { timeout: 45_000 },
  );
  const statusText = squeeze(await page.locator("p.status").innerText());
  const sidebar = squeeze(await page.locator(".sidebar-bottom").innerText());
  const dayAfter = await apiGet<Day>("/api/day", session);
  const activityAfter = await apiGet<{ receipts: unknown[] }>("/api/activity", session);
  const recheckEnabled = await page
    .getByRole("button", { name: /recheck now/i })
    .isEnabled();
  const evidence = JSON.stringify(
    {
      statusText,
      sidebar,
      runStatus: dayAfter.last_run?.status ?? null,
      travelBlocksBefore: dayBefore.travel_blocks.length,
      travelBlocksAfter: dayAfter.travel_blocks.length,
      decisionsAfter: dayAfter.decisions.length,
      activityReceiptsAfter: activityAfter.receipts.length,
      recheckButtonEnabledWhilePaused: recheckEnabled,
    },
    null,
    2,
  );
  audit.step("recheck while paused", `status="${statusText}"`, evidence);

  const runStatus = String(dayAfter.last_run?.status ?? "");
  const planUnchanged =
    dayAfter.travel_blocks.length === dayBefore.travel_blocks.length &&
    dayAfter.decisions.length === dayBefore.decisions.length;
  if (
    dayAfter.last_run == null &&
    planUnchanged &&
    /travel plan updated/i.test(statusText)
  ) {
    audit.medium(
      "Recheck while paused claims the plan was updated, but no run completed",
      `The page said "${statusText}", yet GET /api/day still has no last_run (never completed), the same ${dayAfter.travel_blocks.length} travel blocks and ${activityAfter.receipts.length} activity receipts as before, and the sidebar still says the session is paused. A paused user is told the check succeeded.`,
      evidence,
    );
  } else if (/paused|superseded/i.test(runStatus) && !/paused|superseded/i.test(statusText)) {
    audit.medium(
      "Recheck while paused reports success",
      `The run finished as "${runStatus}" and no plan was produced, but the page says "${statusText}".`,
      evidence,
    );
  } else {
    audit.ok("Paused recheck tells the truth", `status="${statusText}", run="${runStatus}"`);
  }

  await page.getByRole("button", { name: "Resume automation" }).click();
  const resumed = await apiGet<Settings>("/api/me", session);
  audit.step("resume automation", `enabled=${resumed.enabled}`);

  // Control: with automation on, the same button does produce a plan, which
  // proves the paused attempt above was a no-op rather than a slow success.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await page.waitForFunction(
    () => {
      const node = document.querySelector("p.status");
      const text = node?.textContent?.trim() ?? "";
      return text.length > 0 && !/planning travel/i.test(text);
    },
    undefined,
    { timeout: 45_000 },
  );
  const dayResumed = await apiGet<Day>("/api/day", session);
  const statusResumed = squeeze(await page.locator("p.status").innerText());
  audit.step(
    "recheck after resume",
    `status="${statusResumed}"`,
    JSON.stringify(
      {
        runStatus: dayResumed.last_run?.status ?? null,
        travelBlocks: dayResumed.travel_blocks.length,
        decisions: dayResumed.decisions.length,
      },
      null,
      2,
    ),
  );
  if (dayResumed.last_run == null && dayResumed.travel_blocks.length === 0) {
    audit.high(
      "Recheck does nothing even after resuming",
      "With automation back on, Recheck now still produced no run and no travel blocks.",
      JSON.stringify({ statusResumed, day: dayResumed }, null, 2),
    );
  } else {
    audit.ok(
      "Recheck after resume produces a plan",
      `last_run=${dayResumed.last_run?.status ?? "null"}, travel blocks=${dayResumed.travel_blocks.length}, decisions=${dayResumed.decisions.length}`,
    );
  }

  watch.assertClean("paused recheck");
  await audit.shot(page, "after-resume");
});

test.describe("narrow viewport", () => {
  test.use({ viewport: { width: 375, height: 812 } });

  test("day view, settings panel and editor fit 375px without clipping", async ({ page }) => {
    const watch = instrument(page, audit);
    const session = await createSession();
    if (!(await openDay(page, session))) return;
    await audit.shot(page, "mobile-day");

    const overflow = () =>
      page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
    const outOfViewport = async () =>
      page.evaluate(() => {
        const nodes = Array.from(
          document.querySelectorAll<HTMLElement>(
            'a, button, input, select, textarea, [role="button"], [role="link"]',
          ),
        );
        return nodes
          .map((node) => {
            const rect = node.getBoundingClientRect();
            const style = window.getComputedStyle(node);
            const visible =
              rect.width > 0 &&
              rect.height > 0 &&
              style.visibility !== "hidden" &&
              style.display !== "none";
            if (!visible) return null;
            const label =
              node.getAttribute("aria-label") ||
              node.textContent?.trim().replace(/\s+/g, " ").slice(0, 40) ||
              node.getAttribute("type") ||
              "unnamed";
            return {
              label,
              box: [
                Math.round(rect.x),
                Math.round(rect.y),
                Math.round(rect.width),
                Math.round(rect.height),
              ],
              clipped: rect.right > window.innerWidth + 1 || rect.left < -1,
              small: rect.height < 32 || rect.width < 32,
            };
          })
          .filter(Boolean);
      });

    const dayOverflow = await overflow();
    const dayControls = await outOfViewport();
    audit.step("375px day view", JSON.stringify(dayOverflow), JSON.stringify(dayControls));
    if (dayOverflow.scrollWidth > dayOverflow.clientWidth) {
      audit.medium(
        "Day view scrolls sideways at 375px",
        `document.scrollWidth=${dayOverflow.scrollWidth} exceeds clientWidth=${dayOverflow.clientWidth}.`,
        JSON.stringify(dayOverflow),
      );
    } else {
      audit.ok("Day view fits 375px", `scrollWidth=${dayOverflow.scrollWidth}`);
    }

    const panel = await openSettings(page);
    if (!panel) return;
    await audit.shot(page, "mobile-settings");
    const settingsOverflow = await overflow();
    const settingsControls = await outOfViewport();
    audit.step(
      "375px settings panel",
      JSON.stringify(settingsOverflow),
      JSON.stringify(settingsControls),
    );
    if (settingsOverflow.scrollWidth > settingsOverflow.clientWidth) {
      audit.medium(
        "Settings panel scrolls sideways at 375px",
        `document.scrollWidth=${settingsOverflow.scrollWidth} exceeds clientWidth=${settingsOverflow.clientWidth}.`,
        JSON.stringify(settingsOverflow),
      );
    } else {
      audit.ok("Settings panel fits 375px", `scrollWidth=${settingsOverflow.scrollWidth}`);
    }
    const clippedSettings = settingsControls.filter((entry) => entry && entry.clipped);
    if (clippedSettings.length) {
      audit.medium(
        "Settings controls are clipped at 375px",
        "Controls extend past the viewport edge and cannot be reached by horizontal scrolling of the page.",
        JSON.stringify(clippedSettings),
      );
    }
    await page.keyboard.press("Escape");
    if ((await panel.count()) > 0) {
      await panel
        .getByRole("button", { name: "Cancel", exact: true })
        .click()
        .catch(() => {});
    }

    await page.getByRole("button", { name: "Edit", exact: true }).first().click();
    const editor = page.locator("form.event-editor");
    if (!(await editor.isVisible().catch(() => false))) {
      audit.high(
        "Event editor did not open at 375px",
        "Clicking Edit on a timeline event did not reveal the event editor form.",
        squeeze(await page.locator("body").innerText().catch(() => "no body")),
      );
      return;
    }
    await audit.shot(page, "mobile-editor");
    const editorOverflow = await overflow();
    const editorControls = await outOfViewport();
    audit.step("375px editor", JSON.stringify(editorOverflow), JSON.stringify(editorControls));
    if (editorOverflow.scrollWidth > editorOverflow.clientWidth) {
      audit.medium(
        "Event editor scrolls sideways at 375px",
        `document.scrollWidth=${editorOverflow.scrollWidth} exceeds clientWidth=${editorOverflow.clientWidth}.`,
        JSON.stringify(editorOverflow),
      );
    } else {
      audit.ok("Event editor fits 375px", `scrollWidth=${editorOverflow.scrollWidth}`);
    }

    const tiny = [...dayControls, ...settingsControls, ...editorControls].filter(
      (entry) => entry && entry.small && !entry.clipped,
    );
    if (tiny.length) {
      audit.low(
        "Small tap targets on the 375px control surface",
        "Controls measured below the 32px minimum; listed with their boxes.",
        JSON.stringify(tiny),
      );
    }

    const controls = await inventory(page);
    audit.step("375px editor inventory", `${controls.length} controls`, JSON.stringify(controls));
    watch.assertClean("375px control surface");
  });
});

audit.step("base url", LIVE_BASE);
