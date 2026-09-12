// Branch-owner end-to-end functional pass over the deployed site.
// Drives the documented judge path (submission/testing-instructions.md Option A)
// through the real UI and cross-checks the DOM against the live API.
import {
  Audit,
  apiGet,
  createSession,
  expect,
  instrument,
  inventory,
  openWithSession,
  squeeze,
} from "./harness";
import { test } from "@playwright/test";

const audit = new Audit("root-functional-pass");

// Every interaction is bounded: an unbounded click waits for the whole test
// timeout and hides which step actually stalled.
test.use({ actionTimeout: 15_000, navigationTimeout: 45_000 });
test.describe.configure({ timeout: 180_000 });

test.afterAll(async () => {
  audit.write();
});

interface DayShape {
  date: string;
  label: string;
  source_events: { occurrence_id: string; title: string; start: string; end: string }[];
  travel_blocks: {
    journey_key: string;
    start: string;
    end: string;
    padding_minutes: number | null;
    origin_occurrence_id: string | null;
    destination_occurrence_id: string | null;
  }[];
  decisions: {
    id: string;
    reason: string;
    allowed_actions: string[];
    calculated_facts: Record<string, unknown>;
  }[];
  last_run: { status: string; safe_failure_code?: string | null } | null;
}

interface ActivityShape {
  receipts: { id: string; operation: string; outcome: string; timestamp: string }[];
}

interface SettingsShape {
  enabled: boolean;
  padding_minutes: number;
  time_zone: string;
  earliest_departure: string | null;
  start_place: { id: string; label: string } | null;
}

const sessionKey = (id: string) => id;

async function openDay(page: Parameters<typeof openWithSession>[0], sessionId: string) {
  await openWithSession(page, sessionKey(sessionId));
  await expect(page.locator("#timeline")).toBeVisible({ timeout: 30_000 });
}

// Clicks "Recheck now" and waits for the run to settle, tolerating the short
// "Checking..." window disappearing before we can observe it.
async function recheck(page: Parameters<typeof openWithSession>[0]) {
  const button = page.getByRole("button", { name: /recheck now/i });
  await button.click({ timeout: 15_000 });
  await page
    .getByRole("button", { name: /checking/i })
    .waitFor({ state: "visible", timeout: 3_000 })
    .catch(() => undefined);
  await expect(button).toBeEnabled({ timeout: 120_000 });
  await expect(button).toHaveText(/recheck now/i, { timeout: 120_000 });
  const alert = page.locator(".error[role='alert']");
  if (await alert.isVisible().catch(() => false)) {
    const text = squeeze(await alert.innerText().catch(() => ""));
    audit.high(
      "Recheck surfaced an error to the user",
      `The day view shows the error alert: "${text}"`,
    );
    throw new Error(`recheck failed in the UI: ${text}`);
  }
}

test("judge path: landing page affordances all behave", async ({ page }) => {
  const watch = instrument(page, audit);
  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  audit.step("GET /", String(response?.status() ?? "no response"));

  if ((response?.status() ?? 0) !== 200) {
    audit.blocker(
      "Landing page did not return 200",
      `The deployed root responded with ${response?.status()}.`,
    );
  } else {
    audit.ok("Landing page reachable", "GET / -> 200");
  }

  const controls = await inventory(page);
  audit.dump("landing-inventory", controls);
  audit.step(
    "landing controls",
    String(controls.length),
    controls.map((entry) => `${entry.tag}:${entry.label}`).join(" | "),
  );

  const expectations: [string, () => ReturnType<typeof page.getByRole>][] = [
    ["Try a sample day", () => page.getByRole("button", { name: /try a sample day/i })],
    ["Connect Google Calendar", () => page.getByRole("link", { name: /connect google calendar/i })],
    ["How it works", () => page.getByRole("link", { name: /how it works/i })],
    ["Skip to get started", () => page.getByRole("link", { name: /skip to get started/i })],
  ];
  for (const [name, locate] of expectations) {
    try {
      await expect(locate()).toBeVisible({ timeout: 5_000 });
      audit.ok(`Landing control present: ${name}`);
    } catch (error) {
      audit.high(`Landing control missing: ${name}`, String(error));
    }
  }

  // Anchor navigation must move the viewport, not just change the URL.
  try {
    await page.getByRole("link", { name: /how it works/i }).click({ timeout: 15_000 });
    await page.waitForTimeout(300);
    const howTop = await page
      .locator("#how-it-works")
      .evaluate((node) => node.getBoundingClientRect().top);
    if (Math.abs(howTop) > 200) {
      audit.low(
        '"How it works" anchor does not scroll the section into view',
        `#how-it-works top is ${Math.round(howTop)}px after the click.`,
      );
    } else {
      audit.ok('"How it works" anchor scrolls to the section');
    }
  } catch (error) {
    audit.medium('"How it works" anchor click failed', String(error));
  }

  // The skip link stays visually hidden until it receives keyboard focus, so
  // focus it first (that is the whole point of a skip link) and check it.
  try {
    const skipLink = page.getByRole("link", { name: /skip to get started/i });
    await skipLink.focus();
    const focused = await skipLink.evaluate((node) => {
      const style = window.getComputedStyle(node);
      const rect = node.getBoundingClientRect();
      return {
        outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
        onScreen: rect.left >= 0 && rect.top >= 0 && rect.width > 0 && rect.height > 0,
      };
    });
    audit.step("skip link while focused", "captured", JSON.stringify(focused));
    if (!focused.onScreen) {
      audit.medium(
        "Skip link never becomes visible, even when focused",
        `Focused style reports ${JSON.stringify(focused)}.`,
      );
    } else {
      audit.ok("Skip link becomes visible on focus", JSON.stringify(focused));
    }
    await skipLink.click({ timeout: 15_000 });
    await page.waitForTimeout(300);
    const heroTop = await page.locator("#get-started").evaluate((node) => node.getBoundingClientRect().top);
    if (Math.abs(heroTop) > 200) {
      audit.low(
        '"Skip to get started" anchor does not move the viewport',
        `#get-started top is ${Math.round(heroTop)}px after the click.`,
      );
    } else {
      audit.ok('"Skip to get started" anchor moves the viewport');
    }
  } catch (error) {
    audit.medium('"Skip to get started" anchor click failed', String(error));
  }

  // The Google entry point must at least target the OAuth start route.
  const connectHref = await page
    .getByRole("link", { name: /connect google calendar/i })
    .getAttribute("href");
  if (connectHref !== "/api/auth/google/start") {
    audit.medium(
      "Connect Google Calendar link does not target the OAuth start route",
      `href=${connectHref}`,
    );
  } else {
    audit.ok("Connect Google Calendar targets /api/auth/google/start");
  }

  watch.assertClean("landing page");
  audit.step("landing api calls", String(watch.apiCalls.length), JSON.stringify(watch.apiCalls));
});

test("judge path: sample day, first recheck, decision and evidence", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);

  const label = await page.locator(".day-heading .label").innerText();
  if (!/sample calendar/i.test(label)) {
    audit.high("Sample day label missing or wrong", `Day label reads "${label}".`);
  } else {
    audit.ok("Sample day label present", label);
  }

  const mode = squeeze(await page.locator(".mode-badge").innerText());
  if (!/sample workspace/i.test(mode)) {
    audit.medium("Workspace badge does not say Sample workspace", mode);
  } else {
    audit.ok("Workspace badge identifies the sample workspace", mode);
  }

  const stats = await page.locator(".day-stats > div").allInnerTexts();
  audit.dump("day-stats", stats);
  const statText = squeeze(stats.join(" | "));
  if (!/03\s*Appointments/i.test(statText.replace(/\s+/g, " "))) {
    // Fall back to a loose check: 3 appointments must be reported somewhere.
    if (!statText.includes("03")) {
      audit.medium("Appointment stat tile does not show 03", statText);
    }
  }

  const rows = await page.locator("#timeline .row").count();
  audit.step("timeline rows before first recheck", String(rows));
  if (rows !== 3) {
    audit.medium(
      "Timeline does not show the three sample appointments before the first check",
      `Found ${rows} rows.`,
    );
  } else {
    audit.ok("Timeline shows the three sample appointments");
  }

  const before = await apiGet<DayShape>("/api/day", session);
  if (before.travel_blocks.length !== 0 || before.decisions.length !== 0) {
    audit.medium(
      "A fresh sample session starts with a plan already in place",
      JSON.stringify({ travel: before.travel_blocks.length, decisions: before.decisions.length }),
    );
  } else {
    audit.ok("Fresh sample session starts with no travel blocks and no decisions");
  }

  await recheck(page);
  const first = await apiGet<DayShape>("/api/day", session);
  audit.dump("day-after-first-recheck", first);

  const domTravel = await page.locator("#timeline .row.travel").count();
  if (domTravel !== 1 || first.travel_blocks.length !== 1) {
    audit.blocker(
      "First recheck does not produce exactly one travel block",
      `DOM shows ${domTravel} travel rows; API reports ${first.travel_blocks.length} blocks.`,
    );
  } else {
    audit.ok("First recheck produces exactly one travel block");
  }

  if (first.decisions.length !== 1) {
    audit.high(
      "First recheck does not produce exactly one decision",
      `API reports ${first.decisions.length} decisions: ${JSON.stringify(first.decisions)}`,
    );
  } else {
    audit.ok("First recheck produces exactly one decision");
  }

  const decision = first.decisions[0];
  if (decision) {
    const card = page.locator(`#decision-${decision.id}`);
    if (!(await card.isVisible().catch(() => false))) {
      audit.high(
        "Decision returned by the API is not rendered in the decision panel",
        `Expected #decision-${decision.id}. Panel HTML: ${squeeze(
          await page.locator(".decisions").innerText().catch(() => "(no panel)"),
        )}`,
      );
    }
    const cardText = squeeze(await card.innerText().catch(() => ""));
    audit.step("first decision card", "captured", cardText);
    if (decision.reason === "insufficient_time") {
      if (!/shortfall:\s*10 minutes/i.test(cardText)) {
        audit.high(
          "Shortfall wording does not report the documented 10 minutes",
          cardText,
        );
      } else {
        audit.ok("Decision card reports the documented 10-minute shortfall", cardText);
      }
    } else {
      audit.medium(
        "Sample decision reason differs from the documented insufficient_time",
        `reason=${decision.reason}`,
      );
    }

    const actions = await card.locator("button, a").allInnerTexts();
    audit.step("first decision actions", "captured", actions.join(" | "));
    for (const expected of [/edit appointments/i, /skip this journey/i]) {
      if (!actions.some((text) => expected.test(text))) {
        audit.high(
          `Expected decision action missing: ${expected}`,
          `Actions rendered: ${actions.join(" | ") || "(none)"}`,
        );
      }
    }
  }

  const receipts = await page.locator("#activity li").count();
  if (receipts < 1) {
    audit.medium(
      "Activity shows no receipt after the first check",
      squeeze(await page.locator("#activity").innerText()),
    );
  } else {
    audit.ok("Activity records the first check", `${receipts} receipt(s)`);
  }

  const lastRun = squeeze(await page.locator(".last-run").innerText().catch(() => ""));
  audit.step("last run line", "captured", lastRun);
  if (!/completed|needs_input|needs input/i.test(lastRun)) {
    audit.medium("Last-run line does not show a terminal status", lastRun);
  }

  watch.assertClean("sample day first recheck");
  await audit.shot(page, "first-recheck-day-view");
});

test("judge path: recheck is idempotent with no edits", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);
  const first = await apiGet<DayShape>("/api/day", session);
  await recheck(page);
  const second = await apiGet<DayShape>("/api/day", session);

  if (second.travel_blocks.length !== first.travel_blocks.length) {
    audit.high(
      "Second recheck duplicates or drops travel blocks",
      `Before: ${first.travel_blocks.length}, after: ${second.travel_blocks.length}.`,
    );
  } else {
    audit.ok("Second recheck keeps the same number of travel blocks");
  }

  const domTravel = await page.locator("#timeline .row.travel").count();
  if (domTravel !== second.travel_blocks.length) {
    audit.medium(
      "DOM travel-row count disagrees with the API",
      `DOM ${domTravel} vs API ${second.travel_blocks.length}.`,
    );
  }

  const unchanged = squeeze(await page.locator("#activity").innerText());
  audit.step("activity after second check", "captured", unchanged);
  if (!/unchanged/i.test(unchanged)) {
    audit.low(
      "Activity does not show an 'unchanged' outcome for an idempotent recheck",
      unchanged,
    );
  } else {
    audit.ok("Activity reports 'unchanged' for an idempotent recheck");
  }

  const decisions = await page.locator(".decisions .decision").count();
  if (decisions !== 1) {
    audit.medium(
      "Decision card count changed after a no-op recheck",
      `Found ${decisions} decision cards.`,
    );
  }
  watch.assertClean("idempotent recheck");
});

test("judge path: documented edit removes the decision and adds a block", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);

  const appointment = page.locator("#timeline .row.event", { hasText: "Appointment" }).first();
  await expect(appointment).toBeVisible({ timeout: 10_000 });
  await appointment.getByRole("button", { name: /^edit$/i }).click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible({ timeout: 10_000 });

  const startField = editor.getByLabel(/start/i);
  const endField = editor.getByLabel(/end/i);
  const originalStart = await startField.inputValue();
  const originalEnd = await endField.inputValue();
  audit.step("appointment before edit", "captured", `${originalStart}-${originalEnd}`);
  if (originalStart !== "11:00") {
    audit.medium(
      "The documented 11:00 appointment does not start at 11:00 London time",
      `Editor shows start=${originalStart}, end=${originalEnd}.`,
    );
  }

  await startField.fill("10:45");
  await endField.fill("11:15");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await expect(editor).toBeHidden({ timeout: 20_000 });

  await recheck(page);
  const after = await apiGet<DayShape>("/api/day", session);
  audit.dump("day-after-edit", after);

  if (after.travel_blocks.length !== 2) {
    audit.blocker(
      "Documented edit does not produce two travel blocks after recheck",
      `API reports ${after.travel_blocks.length} travel blocks.`,
    );
  } else {
    audit.ok("Documented edit produces two travel blocks");
  }
  if (after.decisions.length !== 0) {
    audit.blocker(
      "Documented edit leaves an open decision",
      `API reports ${after.decisions.length} decisions: ${JSON.stringify(after.decisions)}`,
    );
  } else {
    audit.ok("Documented edit clears the open decision");
  }
  const domTravel = await page.locator("#timeline .row.travel").count();
  const domDecisions = await page.locator(".decisions .decision").count();
  if (domTravel !== after.travel_blocks.length || domDecisions !== after.decisions.length) {
    audit.high(
      "Day view does not match the API after the documented edit",
      `DOM travel=${domTravel}/API ${after.travel_blocks.length}, DOM decisions=${domDecisions}/API ${after.decisions.length}.`,
    );
  } else {
    audit.ok("Day view matches the API after the documented edit");
  }

  watch.assertClean("documented edit");
  await audit.shot(page, "after-documented-edit");
});

test("judge path: skip this journey resolves the decision after a reset", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);

  const skip = page.getByRole("button", { name: /skip this journey/i }).first();
  if (!(await skip.isVisible().catch(() => false))) {
    audit.blocker(
      "Skip this journey is not offered for the sample shortfall",
      squeeze(await page.locator(".decisions").innerText().catch(() => "(no decisions panel)")),
    );
    return;
  }
  await skip.click();
  await expect(skip).toBeHidden({ timeout: 120_000 });

  const after = await apiGet<DayShape>("/api/day", session);
  audit.dump("day-after-skip", after);
  if (after.decisions.length !== 0) {
    audit.high(
      "Skip this journey does not clear the decision",
      `API still reports ${after.decisions.length} decisions.`,
    );
  } else {
    audit.ok("Skip this journey clears the decision");
  }
  if (after.travel_blocks.length !== 0) {
    audit.medium(
      "Skip this journey leaves a travel block behind for the skipped journey",
      `API reports ${after.travel_blocks.length} travel blocks.`,
    );
  }

  const activity = squeeze(await page.locator("#activity").innerText());
  audit.step("activity after skip", "captured", activity);
  if (!/skip/i.test(activity)) {
    audit.low("Activity does not mention the skipped journey", activity);
  }

  // A recheck after the skip must not resurrect the journey.
  await recheck(page);
  const afterRecheck = await apiGet<DayShape>("/api/day", session);
  if (afterRecheck.decisions.length !== 0) {
    audit.high(
      "A recheck resurrects a decision the user already skipped",
      JSON.stringify(afterRecheck.decisions),
    );
  } else {
    audit.ok("A recheck does not resurrect the skipped decision");
  }
  watch.assertClean("skip journey");
});

test("judge path: arrival buffer setting updates UI, API and the plan", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);

  await page.getByRole("button", { name: /settings/i }).first().click();
  const panel = page.locator("#travel-settings");
  await expect(panel).toBeVisible({ timeout: 10_000 });

  const field = panel.getByLabel(/arrival buffer/i);
  const before = await field.inputValue();
  audit.step("arrival buffer default", "captured", before);

  // Out-of-range values must be rejected with a visible message.
  await field.fill("99");
  await panel.getByRole("button", { name: /save settings/i }).click();
  const rangeError = await panel.getByRole("alert").innerText().catch(() => "");
  if (!/between 0 and 60/i.test(rangeError)) {
    audit.medium(
      "Out-of-range arrival buffer is not rejected with an explanatory message",
      `Message shown: "${squeeze(rangeError)}"`,
    );
  } else {
    audit.ok("Out-of-range arrival buffer is rejected", squeeze(rangeError));
  }

  await field.fill("20");
  await panel.getByRole("button", { name: /save settings/i }).click();
  await expect(panel).toBeHidden({ timeout: 20_000 });

  const statText = squeeze(await page.locator(".day-stats").innerText());
  if (!/20\s*min/i.test(statText.replace(/\s+/g, " "))) {
    audit.medium("Arrival-buffer stat tile did not update after saving", statText);
  } else {
    audit.ok("Arrival-buffer stat tile updates after saving", statText);
  }
  const note = squeeze(await page.locator(".journey-note").innerText());
  if (!/20 minutes/i.test(note)) {
    audit.medium("Journey note does not reflect the saved arrival buffer", note);
  } else {
    audit.ok("Journey note reflects the saved arrival buffer");
  }

  const settings = await apiGet<SettingsShape>("/api/me", session);
  if (settings.padding_minutes !== 20) {
    audit.high(
      "Saved arrival buffer is not persisted by the API",
      `GET /api/me -> padding_minutes=${settings.padding_minutes}`,
    );
  } else {
    audit.ok("Arrival buffer persists through the API");
  }

  await page.reload({ waitUntil: "networkidle" });
  const afterReload = squeeze(await page.locator(".day-stats").innerText());
  if (!/20\s*min/i.test(afterReload.replace(/\s+/g, " "))) {
    audit.medium("Arrival buffer resets after a reload", afterReload);
  } else {
    audit.ok("Arrival buffer survives a reload");
  }

  watch.assertClean("settings save");
  await audit.shot(page, "after-settings-save");
});

test("judge path: pause and resume automation is coherent", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);

  await page.getByRole("button", { name: /pause automation/i }).click();
  await expect(page.getByRole("button", { name: /resume automation/i })).toBeVisible({
    timeout: 30_000,
  });
  const pausedNote = squeeze(await page.locator(".automation-note").innerText());
  const pausedSettings = await apiGet<SettingsShape>("/api/me", session);
  if (pausedSettings.enabled !== false || !/paused/i.test(pausedNote)) {
    audit.high(
      "Pause automation does not leave the app in a paused state",
      `note="${pausedNote}", enabled=${pausedSettings.enabled}`,
    );
  } else {
    audit.ok("Pause automation updates the sidebar and the API");
  }

  const recheckWhilePaused = page.getByRole("button", { name: /recheck now/i });
  const recheckEnabled = await recheckWhilePaused.isEnabled();
  audit.step("recheck enabled while paused", String(recheckEnabled));
  if (recheckEnabled) {
    await recheck(page);
    const afterPausedRun = await apiGet<DayShape>("/api/day", session);
    if (afterPausedRun.decisions.length > 1) {
      audit.medium(
        "Manual recheck while paused accumulates duplicate decisions",
        JSON.stringify(afterPausedRun.decisions.map((entry) => entry.reason)),
      );
    }
  }

  await page.getByRole("button", { name: /resume automation/i }).click();
  await expect(page.getByRole("button", { name: /pause automation/i })).toBeVisible({
    timeout: 30_000,
  });
  const resumed = await apiGet<SettingsShape>("/api/me", session);
  if (resumed.enabled !== true) {
    audit.high("Resume automation does not re-enable automation", JSON.stringify(resumed));
  } else {
    audit.ok("Resume automation re-enables automation");
  }
  watch.assertClean("pause/resume");
});

test("judge path: reset sample restores the starting state", async ({ page }) => {
  const watch = instrument(page, audit);
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);
  await audit.shot(page, "before-reset");

  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/sample reset to its starting state/i)).toBeVisible({
    timeout: 30_000,
  });

  const day = await apiGet<DayShape>("/api/day", session);
  const activity = await apiGet<ActivityShape>("/api/activity", session);
  if (day.travel_blocks.length !== 0 || day.decisions.length !== 0) {
    audit.high(
      "Reset sample leaves plan state behind",
      `travel=${day.travel_blocks.length}, decisions=${day.decisions.length}`,
    );
  } else {
    audit.ok("Reset sample clears travel blocks and decisions");
  }
  if (activity.receipts.length !== 0) {
    audit.medium(
      "Reset sample leaves activity receipts behind",
      `${activity.receipts.length} receipts remain.`,
    );
  } else {
    audit.ok("Reset sample clears activity");
  }

  const statText = squeeze(await page.locator(".day-stats").innerText());
  if (!/00/.test(statText)) {
    audit.medium("Reset sample does not refresh the day statistics", statText);
  }
  watch.assertClean("reset sample");
  await audit.shot(page, "after-reset");
});

test("narrow viewport: day view and its controls stay usable", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.setViewportSize({ width: 375, height: 812 });
  const session = await createSession();
  await openDay(page, session);
  await recheck(page);

  const metrics = await page.evaluate(() => {
    const root = document.documentElement;
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>("button, a, input, select"),
    )
      .filter((node) => node.offsetParent !== null)
      .map((node) => {
        const rect = node.getBoundingClientRect();
        return {
          label:
            node.getAttribute("aria-label") ?? (node.textContent ?? "").trim().slice(0, 40),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          x: Math.round(rect.x),
        };
      });
    return {
      scrollWidth: root.scrollWidth,
      clientWidth: root.clientWidth,
      controls,
    };
  });
  audit.dump("narrow-viewport-metrics", metrics);

  if (metrics.scrollWidth > metrics.clientWidth + 1) {
    audit.medium(
      "Day view has horizontal overflow at 375px",
      `scrollWidth=${metrics.scrollWidth} clientWidth=${metrics.clientWidth}`,
    );
  } else {
    audit.ok("Day view has no horizontal overflow at 375px");
  }

  const small = metrics.controls.filter((control) => control.height < 32 && control.width > 0);
  if (small.length) {
    audit.low(
      `${small.length} control(s) are shorter than 32px at 375px`,
      small.map((control) => `${control.label} (${control.width}x${control.height})`).join(" | "),
    );
  }

  const offscreen = metrics.controls.filter(
    (control) => control.x < 0 || control.x + control.width > metrics.clientWidth + 1,
  );
  if (offscreen.length) {
    audit.medium(
      `${offscreen.length} control(s) sit outside the 375px viewport`,
      offscreen.map((control) => `${control.label} x=${control.x} w=${control.width}`).join(" | "),
    );
  } else {
    audit.ok("Every visible control fits inside the 375px viewport");
  }

  await audit.shot(page, "375px-day-view");
  watch.assertClean("narrow viewport");
});
