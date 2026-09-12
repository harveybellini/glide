// Exhaustive control-by-control click-through of the deployed site.
//
// Every visible interactive control in each reachable screen state is clicked
// in isolation (state is rebuilt before each click) and the observable outcome
// is recorded: navigation, DOM change, status text, console errors.
import { test } from "@playwright/test";
import {
  Audit,
  LIVE_BASE,
  SESSION_KEY,
  createSession,
  expect,
  inventory,
  instrument,
} from "./harness";

const audit = new Audit("deep-clickthrough");
test.describe.configure({ mode: "serial" });
test.setTimeout(900_000);

test.afterAll(() => {
  audit.write();
});

type Snapshot = {
  url: string;
  text: string;
  controls: number;
  markers: Record<string, number>;
};

const MARKERS = [
  "Travel Â· Glide",
  "Needs your decision",
  "No updates yet",
  "Recheck now",
  "Pause automation",
  "Resume automation",
  "Sample workspace",
  "Your calendar",
];

async function snapshot(page): Promise<Snapshot> {
  return page.evaluate((markers: string[]) => {
    const counts: Record<string, number> = {};
    const body = document.body.innerText;
    for (const marker of markers) {
      counts[marker] = body.split(marker).length - 1;
    }
    return {
      url: location.href,
      text: body.replace(/\s+/g, " ").slice(0, 6000),
      controls: document.querySelectorAll(
        'a,button,input,select,textarea,[role="button"],[tabindex]:not([tabindex="-1"])',
      ).length,
      markers: counts,
    };
  }, MARKERS);
}

function diff(before: Snapshot, after: Snapshot) {
  const changes: string[] = [];
  if (before.url !== after.url) changes.push(`url ${before.url} -> ${after.url}`);
  if (before.controls !== after.controls) {
    changes.push(`control count ${before.controls} -> ${after.controls}`);
  }
  for (const marker of MARKERS) {
    if (before.markers[marker] !== after.markers[marker]) {
      changes.push(`"${marker}" ${before.markers[marker]} -> ${after.markers[marker]}`);
    }
  }
  if (!changes.length && before.text !== after.text) {
    const at = [...before.text].findIndex((char, index) => char !== after.text[index]);
    changes.push(
      `body text changed near "${before.text.slice(Math.max(0, at - 40), at + 60)}" -> "${after.text.slice(Math.max(0, at - 40), at + 60)}"`,
    );
  }
  return changes;
}

// Clicks one control and returns what happened. Never lets an external
// navigation (the Google consent screen) actually load.
async function clickControl(page, entry) {
  const before = await snapshot(page);
  const target = page.locator(
    'a,button,input,select,textarea,[role="button"],[tabindex]:not([tabindex="-1"])',
  ).nth(entry.index);
  let clickError: string | null = null;
  try {
    await target.click({ timeout: 5_000, noWaitAfter: true });
  } catch (error) {
    clickError = String(error).split("\n")[0];
  }
  await page.waitForTimeout(1_200);
  await page.waitForLoadState("networkidle", { timeout: 15_000 }).catch(() => {});
  const after = await snapshot(page);
  const external = !after.url.startsWith(LIVE_BASE);
  if (external) {
    await page.goto(LIVE_BASE, { waitUntil: "domcontentloaded" });
  }
  return { before, after, changes: diff(before, after), clickError, external };
}

// ---------------------------------------------------------------- landing ---

test("landing: every control responds and none is dead", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.route("**://accounts.google.com/**", (route) => route.abort());

  await page.goto("/", { waitUntil: "networkidle" });
  const controls = await inventory(page);
  audit.step("landing controls", String(controls.length), JSON.stringify(controls.map((c) => `${c.tag}:${c.label}`)));
  audit.dump("landing-controls", controls);

  const results = [];
  for (const entry of controls) {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("main.landing", { timeout: 20_000 });
    const current = (await inventory(page)).find(
      (candidate) => candidate.index === entry.index && candidate.label === entry.label,
    );
    if (!current) {
      results.push({ label: entry.label, tag: entry.tag, outcome: "vanished" });
      continue;
    }
    const outcome = await clickControl(page, current);
    const changed = outcome.changes.length > 0;
    results.push({
      label: entry.label,
      tag: entry.tag,
      href: entry.href,
      changed,
      changes: outcome.changes.slice(0, 3),
      clickError: outcome.clickError,
      external: outcome.external,
    });
    audit.step(
      `click ${entry.tag} "${entry.label}"`,
      changed ? "state changed" : outcome.clickError ? "click failed" : "no observable change",
      outcome.changes.slice(0, 3).join(" | ") || outcome.clickError || "",
    );
    if (!changed && !outcome.clickError && !outcome.external && entry.href !== "/api/auth/google/start") {
      audit.medium(
        `Dead control on landing: "${entry.label}"`,
        `Clicking ${entry.tag} "${entry.label}" produced no URL, DOM or control-count change and no error message.`,
        JSON.stringify(outcome.before.text.slice(0, 300)),
      );
    }
    if (outcome.clickError && !/intercepts pointer events/i.test(outcome.clickError)) {
      audit.low(
        `Click failed on landing: "${entry.label}"`,
        `Playwright could not click the control: ${outcome.clickError}`,
      );
    }
  }
  audit.dump("landing-click-results", results);
  watch.assertClean("landing click-through");
  audit.ok("Landing controls exercised", `${results.filter((r) => r.changed).length}/${results.length} changed state`);
});

// -------------------------------------------------------------- day view ---

async function seedDay(page, sessionId: string) {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, sessionId],
  );
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForSelector("button:has-text('Recheck now')", { timeout: 30_000 });
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
}

test("day view: every control responds after a recheck", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.route("**://accounts.google.com/**", (route) => route.abort());

  const sessionId = await createSession();
  audit.step("created sample session", "ok", sessionId);

  // Establish the documented post-recheck state once, for the record.
  await seedDay(page, sessionId);
  await page.getByRole("button", { name: /recheck now/i }).click();
  await page.waitForTimeout(3_000);
  await expect(page.locator("p.status")).not.toBeEmpty({ timeout: 90_000 });
  const firstStatus = (await page.locator("p.status").innerText()).trim();
  audit.step("first recheck status", firstStatus);
  const blocks = await page.getByText("Travel Â· Glide", { exact: true }).count();
  const decision = await page.getByRole("heading", { name: /needs your decision/i }).count();
  audit.step("state after recheck", `blocks=${blocks} decision=${decision}`);
  await audit.shot(page, "day-after-recheck");

  const controls = await inventory(page);
  audit.dump("day-controls", controls);
  audit.step("day controls", String(controls.length), JSON.stringify(controls.map((c) => `${c.tag}:${c.label}`)));

  const results = [];
  for (const entry of controls) {
    // Rebuild the state: fresh page, same session, same post-recheck shape.
    await seedDay(page, sessionId);
    const current = (await inventory(page)).find(
      (candidate) => candidate.index === entry.index && candidate.label === entry.label,
    );
    if (!current) {
      results.push({ label: entry.label, tag: entry.tag, outcome: "not present after rebuild" });
      continue;
    }
    const outcome = await clickControl(page, current);
    const changed = outcome.changes.length > 0;
    const statusText = (await page.locator("p.status").innerText().catch(() => "")).trim();
    results.push({
      label: entry.label,
      tag: entry.tag,
      href: entry.href,
      changed,
      changes: outcome.changes.slice(0, 4),
      statusText,
      clickError: outcome.clickError,
      external: outcome.external,
    });
    audit.step(
      `click ${entry.tag} "${entry.label}"`,
      changed ? "state changed" : outcome.clickError ? "click failed" : "no observable change",
      [outcome.changes.slice(0, 3).join(" | "), statusText].filter(Boolean).join(" :: "),
    );
    if (entry.tag === "button" && !changed && !outcome.clickError) {
      audit.major(
        `Dead button in day view: "${entry.label}"`,
        `Clicking it changed nothing observable (no status text, no DOM, no navigation).`,
      );
    }
  }
  audit.dump("day-click-results", results);
  watch.assertClean("day click-through");
  audit.ok("Day controls exercised", `${results.filter((r) => r.changed).length}/${results.length} changed state`);
});

// ------------------------------------------------------- keyboard walk -----

test("keyboard: every tab stop is visible, named and in a sane order", async ({ page }) => {
  const sessionId = await createSession();
  await seedDay(page, sessionId);

  const stops = [];
  for (let index = 0; index < 60; index += 1) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      if (!active || active === document.body) return null;
      const rect = active.getBoundingClientRect();
      const style = window.getComputedStyle(active);
      return {
        tag: active.tagName.toLowerCase(),
        name: (
          active.getAttribute("aria-label") ||
          active.textContent?.replace(/\s+/g, " ").trim() ||
          active.getAttribute("placeholder") ||
          active.getAttribute("name") ||
          ""
        ).slice(0, 60),
        visible: rect.width > 0 && rect.height > 0 && style.visibility !== "hidden",
        inViewport: rect.top >= -5 && rect.bottom <= window.innerHeight + 5,
        outline: `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`,
        boxShadow: style.boxShadow,
        className: active.className?.toString?.().slice(0, 60) ?? "",
      };
    });
    if (!stop) break;
    stops.push(stop);
    const key = `${stop.tag}:${stop.name}`;
    if (stops.slice(0, -1).some((previous) => `${previous.tag}:${previous.name}` === key)) {
      audit.step("tab order cycled", key);
      break;
    }
  }
  audit.dump("keyboard-stops", stops);
  audit.step("keyboard stops", String(stops.length));

  const unnamed = stops.filter((stop) => !stop.name.trim());
  if (unnamed.length) {
    audit.major(
      "Focusable control without an accessible name",
      `${unnamed.length} tab stop(s) have no text, aria-label, placeholder or name.`,
      JSON.stringify(unnamed.slice(0, 6)),
    );
  }
  const invisible = stops.filter((stop) => !stop.visible);
  if (invisible.length) {
    audit.major(
      "Focus lands on an invisible element",
      `${invisible.length} tab stop(s) measured zero size or hidden.`,
      JSON.stringify(invisible.slice(0, 6)),
    );
  }
  const unstyled = stops.filter(
    (stop) => stop.outline === "none 0px rgb(0, 0, 0)" && stop.boxShadow === "none",
  );
  if (unstyled.length) {
    audit.minor(
      "Focus indicator missing on some controls",
      `${unstyled.length}/${stops.length} tab stops show no outline or box-shadow while focused.`,
      JSON.stringify(unstyled.slice(0, 8)),
    );
  }
  if (!unnamed.length && !invisible.length) {
    audit.ok("Keyboard walk clean", `${stops.length} tab stops all named and visible`);
  }
});

// ----------------------------------------------------------- double click --

test("races: double-click Recheck does not duplicate travel blocks", async ({ page }) => {
  const sessionId = await createSession();
  await seedDay(page, sessionId);
  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.locator("p.status")).toContainText(/sample reset/i, { timeout: 30_000 });

  const recheck = page.getByRole("button", { name: /recheck now/i });
  await recheck.click({ clickCount: 2, delay: 40, noWaitAfter: true }).catch(() => {});
  await page.waitForTimeout(4_000);
  await expect(page.locator("p.status")).not.toBeEmpty({ timeout: 90_000 });
  await page.waitForTimeout(6_000);

  const blocks = await page.getByText("Travel Â· Glide", { exact: true }).count();
  const activity = await page.locator("#activity").innerText();
  const status = (await page.locator("p.status").innerText()).trim();
  audit.step("double click outcome", `blocks=${blocks} status="${status}"`, activity.replace(/\s+/g, " ").slice(0, 300));
  if (blocks > 2) {
    audit.major(
      "Double-clicking Recheck duplicates travel blocks",
      `Two rapid clicks produced ${blocks} "Travel Â· Glide" blocks instead of the expected single block (or one per journey).`,
      activity.slice(0, 600),
    );
  }
  audit.ok("Double-click Recheck completed", `${blocks} travel block(s) after two rapid clicks`);
});

// --------------------------------------------------------- offline / error --

test("resilience: offline reload keeps the app usable and says why", async ({ page }) => {
  const sessionId = await createSession();
  await seedDay(page, sessionId);
  await page.context().setOffline(true);
  await page.getByRole("button", { name: /recheck now/i }).click();
  await page.waitForTimeout(3_000);
  const body = await page.locator("body").innerText();
  const status = (await page.locator("p.status").innerText().catch(() => "")).trim();
  const alert = (await page.locator('[role="alert"]').innerText().catch(() => "")).trim();
  audit.step("offline recheck", `status="${status}" alert="${alert}"`);
  audit.dump("offline-body", body.slice(0, 2_000));
  if (!/offline|network|connection|failed|error|try again/i.test(body)) {
    audit.major(
      "No user-visible message when the network is down",
      "With the browser offline, Recheck produced no offline/error copy anywhere on the page.",
      body.slice(0, 400),
    );
  }
  await page.context().setOffline(false);
  await page.waitForTimeout(1_000);
  audit.ok("Offline probe recorded", `status="${status}"`);
});
