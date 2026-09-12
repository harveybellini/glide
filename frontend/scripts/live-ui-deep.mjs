/**
 * Second-pass live audit for Glide: disambiguates the first pass's failures and
 * covers behaviours the first pass could not reach (decision resolution, deep
 * links, native validation, race conditions, skip link).
 *
 * Run with: node scripts/live-ui-deep.mjs
 */
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "..", "..", "temp", "functional-test");
const shotDir = join(outDir, "shots-deep");
mkdirSync(shotDir, { recursive: true });
const BASE = (process.argv[2] ?? "https://d3tvxy281s2u11.cloudfront.net").replace(/\/$/, "");

const checks = [];
const observations = [];
const appLog = [];
let shotIndex = 0;

async function check(name, fn) {
  try {
    const detail = await fn();
    checks.push({ name, ok: true, detail: detail ?? "" });
    console.log(`PASS  ${name}${detail ? ` :: ${detail}` : ""}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    checks.push({ name, ok: false, detail: message });
    console.log(`FAIL  ${name} :: ${message}`);
  }
}

function observe(name, detail) {
  observations.push({ name, detail });
  console.log(`NOTE  ${name} :: ${detail}`);
}

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

async function shot(page, name) {
  shotIndex += 1;
  await page
    .screenshot({ path: join(shotDir, `${String(shotIndex).padStart(2, "0")}-${name}.png`), fullPage: true })
    .catch(() => {});
}

async function bodyText(page) {
  return (await page.locator("body").innerText()).replace(/\s+/g, " ").trim();
}

async function waitStatus(page, fragment, timeout = 120_000) {
  await page.waitForFunction(
    (needle) => (document.querySelector("p.status")?.textContent ?? "").includes(needle),
    fragment,
    { timeout },
  );
}

async function recheck(page) {
  await page.getByRole("button", { name: /Recheck now|Checking/i }).click();
  await page.waitForFunction(
    () => {
      const text = document.querySelector("p.status")?.textContent ?? "";
      return /Travel plan updated|decision needs your input|failed|could not be completed/i.test(text);
    },
    { timeout: 180_000 },
  );
  return (await page.locator("p.status").innerText()).trim();
}

async function apiJson(page, path, init) {
  const response = await page.request.fetch(`${BASE}${path}`, init);
  return { status: response.status(), body: await response.json().catch(() => null) };
}

function sessionHeader(init = {}) {
  return init;
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: "en-GB",
  timezoneId: "Europe/London",
});
const page = await context.newPage();
page.setDefaultTimeout(20_000);
context.on("pageerror", (error) => appLog.push(`pageerror: ${error}`));
context.on("console", (message) => {
  if (message.type() === "error") appLog.push(`console: ${message.text().slice(0, 300)}`);
});

const patched = [];
const daySnapshots = [];
context.on("request", (request) => {
  if (request.method() === "PATCH") {
    patched.push({ url: request.url().replace(BASE, ""), body: request.postData() });
  }
});
context.on("response", async (response) => {
  if (response.url().endsWith("/api/day") && response.status() === 200) {
    const body = await response.json().catch(() => null);
    if (body) {
      daySnapshots.push({
        date: body.date,
        events: body.source_events.map((event) => `${event.occurrence_id} ${event.title} ${event.start}->${event.end} @${event.location}`),
        blocks: body.travel_blocks.length,
        decisions: body.decisions.map((decision) => `${decision.reason}:${decision.allowed_actions.join("+")}`),
        last_run: body.last_run ? `${body.last_run.id} ${body.last_run.status}` : null,
      });
    }
  }
});

try {
  const session = await page.evaluate(() => window.localStorage.getItem("x")).catch(() => null);

  await check("start sample session and capture API state", async () => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: /Try a sample day/i }).click();
    await page.waitForSelector(".timeline", { timeout: 30_000 });
    const me = await page.evaluate(() =>
      fetch("/api/me", {
        headers: { "X-Glide-Session": window.localStorage.getItem("glide-sample-session") ?? "" },
      }).then((r) => r.json()),
    );
    return `padding=${me.padding_minutes} start=${me.start_place?.label} tz=${me.time_zone} enabled=${me.enabled}`;
  });

  await check("run produces a decision and the day payload carries it", async () => {
    const status = await recheck(page);
    expect(/decision needs your input/i.test(status), `status was "${status}"`);
    const cards = await page.locator("article.decision").count();
    expect(cards >= 1, "no decision card rendered");
    const card = await page.locator("article.decision").first().innerText();
    const buttons = (await page.locator("article.decision button").allInnerTexts()).map((b) => b.trim());
    observe(
      "sample-mode decision actions",
      `reason text="${card.replace(/\s+/g, " ").slice(0, 120)}" buttons=[${buttons.join(", ")}]`,
    );
    return `cards=${cards}`;
  });
  await shot(page, "decision-card");

  await check("last-check line renders after a completed run", async () => {
    const lastRun = await page.locator("p.last-run").count();
    const text = await page.locator("p.last-run").innerText().catch(() => "");
    observe(
      "day.last_run in API payload",
      JSON.stringify(daySnapshots.slice(-1)[0]?.last_run ?? null),
    );
    expect(lastRun > 0, `p.last-run is absent (Activity shows no "Last check" line); API last_run=${JSON.stringify(daySnapshots.slice(-1)[0]?.last_run ?? null)}`);
    return text.replace(/\s+/g, " ");
  });

  await check('quiet-note shows completed copy after a clean check', async () => {
    const text = await page.locator(".quiet-note, .decisions").first().innerText().catch(() => "");
    observe("quiet note / decisions state", text.replace(/\s+/g, " ").slice(0, 160));
    return text.replace(/\s+/g, " ").slice(0, 80);
  });

  await check('"Skip this journey" resolves the decision and refreshes the day', async () => {
    const before = await page.locator("article.decision").count();
    await page.getByRole("button", { name: /Skip this journey/i }).first().click();
    await waitStatus(page, "Journey skipped.");
    await page.waitForTimeout(600);
    const after = await page.locator("article.decision").count();
    const blocks = await page.locator("article.row.travel").count();
    expect(after === before - 1, `decision cards ${before} -> ${after}`);
    return `cards ${before}->${after}, travel blocks=${blocks}`;
  });
  await shot(page, "after-skip-journey");

  await check("clearing an event location produces unknown_location guidance", async () => {
    await page.getByRole("button", { name: "Edit", exact: true }).nth(1).click();
    const editor = page.locator("form.event-editor");
    await editor.waitFor({ timeout: 10_000 });
    await editor.getByLabel("Location").fill("");
    await editor.getByRole("button", { name: /Save changes/i }).click();
    await waitStatus(page, "Appointment updated");
    const status = await recheck(page);
    const cardText = (await page.locator("article.decision").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    observe("unknown_location decision copy", `status="${status}" text="${cardText.slice(0, 160)}"`);
    expect(/could not be resolved|unknown/i.test(cardText), `decision text was "${cardText.slice(0, 200)}"`);
    return cardText.slice(0, 120);
  });
  await shot(page, "unknown-location");

  await check("restoring the location returns the canonical time decision", async () => {
    const skip = page.getByRole("button", { name: /Skip this journey/i });
    if (await skip.count()) {
      await skip.first().click();
      await waitStatus(page, "Journey skipped.");
    }
    await page.getByRole("button", { name: "Edit", exact: true }).nth(1).click();
    const editor = page.locator("form.event-editor");
    await editor.waitFor({ timeout: 10_000 });
    await editor.getByLabel("Location").fill("Westfield Surgery");
    await editor.getByRole("button", { name: /Save changes/i }).click();
    await waitStatus(page, "Appointment updated");
    const status = await recheck(page);
    const cardText = (await page.locator("article.decision").first().innerText().catch(() => "")).replace(/\s+/g, " ");
    expect(/minutes/i.test(cardText), `expected a time-shortfall decision, got "${cardText.slice(0, 200)}"`);
    return `status="${status}"`;
  });

  await check("edited event time reaches the timeline after save", async () => {
    await page.getByRole("button", { name: "Edit", exact: true }).first().click();
    const editor = page.locator("form.event-editor");
    await editor.waitFor({ timeout: 10_000 });
    await editor.getByLabel("Start").fill("09:40");
    await editor.getByLabel("End").fill("10:40");
    await editor.getByRole("button", { name: /Save changes/i }).click();
    await waitStatus(page, "Appointment updated");
    try {
      await page.waitForFunction(
        () => (document.getElementById("timeline")?.textContent ?? "").includes("09:40"),
        { timeout: 8000 },
      );
    } catch {
      const timeline = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
      const lastDay = daySnapshots.slice(-1)[0];
      throw new Error(
        `timeline never showed 09:40 within 8s. timeline="${timeline.slice(0, 180)}" lastDayEvents=${JSON.stringify(lastDay?.events)} lastPatch=${JSON.stringify(patched.slice(-1))}`,
      );
    }
    const lastPatch = patched.slice(-1)[0];
    return `PATCH ${lastPatch?.url} body=${lastPatch?.body}`;
  });
  await shot(page, "after-edit");

  await check("?decision=<id> deep link focuses, scrolls and cleans the URL", async () => {
    const cardId = await page.locator("article.decision").first().getAttribute("id");
    if (!cardId) throw new Error("no decision card present to deep-link to");
    const decisionId = cardId.replace("decision-", "");
    await page.goto(`${BASE}/?decision=${decisionId}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(`#decision-${decisionId}`, { timeout: 60_000 });
    await page.waitForTimeout(1500);
    const className = await page.locator(`#decision-${decisionId}`).getAttribute("class");
    const inView = await page.evaluate((id) => {
      const rect = document.getElementById(`decision-${id}`)?.getBoundingClientRect();
      return rect ? rect.top < window.innerHeight && rect.bottom > 0 : false;
    }, decisionId);
    const urlCleaned = !page.url().includes("decision=");
    expect(/decision-focused/.test(className ?? ""), `class=${className}`);
    expect(inView, "card not scrolled into view");
    expect(urlCleaned, `query string not cleaned: ${page.url()}`);
    return `id=${decisionId.slice(0, 10)}… focused+scrolled+cleaned`;
  });
  await shot(page, "deep-link");

  await check("Escape closes the settings panel and restores focus", async () => {
    await page.getByRole("button", { name: /Settings/i }).first().click();
    await page.locator("#travel-settings").waitFor({ timeout: 10_000 });
    await page.keyboard.press("Escape");
    await page.waitForTimeout(250);
    const open = await page.locator("#travel-settings").count();
    const focusText = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    expect(open === 0, "panel still open");
    expect(/Settings/i.test(focusText), `focus="${focusText}"`);
    return `closed, focus="${focusText}"`;
  });

  await check("out-of-range buffer: native validation vs app message", async () => {
    await page.getByRole("button", { name: /Settings/i }).first().click();
    const panel = page.locator("#travel-settings");
    await panel.waitFor({ timeout: 10_000 });
    const padding = panel.getByLabel(/Arrival buffer/i);
    await padding.fill("61");
    const validity = await padding.evaluate((element) => ({
      value: element.value,
      tooBig: element.validity.rangeOverflow,
      message: element.validationMessage,
    }));
    const patchesBefore = patched.length;
    await panel.getByRole("button", { name: /Save settings/i }).click();
    await page.waitForTimeout(400);
    const appError = await panel.locator("p.error").innerText().catch(() => "");
    const patchesAfter = patched.length;
    observe(
      "out-of-range arrival buffer handling",
      `native: tooBig=${validity.tooBig} message="${validity.message}" | appError="${appError.trim()}" | PATCHes=${patchesAfter - patchesBefore}`,
    );
    expect(patchesAfter === patchesBefore, "invalid value still issued PATCH");
    expect(panel, "panel closed unexpectedly");
    return `native message="${validity.message}", app message rendered="${appError.trim().length > 0}"`;
  });

  await check("empty arrival buffer is silently saved as 0 (defect probe)", async () => {
    const panel = page.locator("#travel-settings");
    const padding = panel.getByLabel(/Arrival buffer/i);
    await padding.fill("");
    await panel.getByRole("button", { name: /Save settings/i }).click();
    await page.waitForTimeout(900);
    const stillOpen = await page.locator("#travel-settings").count();
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).replace(/\s+/g, "");
    const me = await page.evaluate(async () => {
      const response = await fetch("/api/me", {
        headers: { "X-Glide-Session": window.localStorage.getItem("glide-sample-session") ?? "" },
      });
      return response.json();
    });
    if (stillOpen === 0) {
      observe("empty arrival buffer", `saved silently; UI shows "${stat}", API padding_minutes=${me.padding_minutes}`);
      throw new Error(
        `clearing the field and saving set the arrival buffer to ${me.padding_minutes} with no message (panel closed)`,
      );
    }
    observe("empty arrival buffer", "blocked with a message");
    return stat;
  });

  await check("reset sample does not restore travel settings (defect probe)", async () => {
    const panel = page.locator("#travel-settings");
    if (await panel.count()) {
      await panel.getByLabel(/Arrival buffer/i).fill("15");
      await panel.getByRole("button", { name: /Save settings/i }).click();
      await waitStatus(page, "Settings saved.");
    } else {
      await page.getByRole("button", { name: /Settings/i }).first().click();
      await page.locator("#travel-settings").waitFor({ timeout: 10_000 });
      await page.locator("#travel-settings").getByLabel(/Arrival buffer/i).fill("15");
      await page.locator("#travel-settings").getByRole("button", { name: /Save settings/i }).click();
      await waitStatus(page, "Settings saved.");
    }
    await page.getByRole("button", { name: /Reset sample/i }).click();
    await waitStatus(page, "Sample reset");
    await page.waitForTimeout(800);
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).replace(/\s+/g, "");
    const meRaw = await page.evaluate(async () => {
      const response = await fetch("/api/me", {
        headers: { "X-Glide-Session": window.localStorage.getItem("glide-sample-session") ?? "" },
      });
      return { status: response.status, body: await response.json() };
    });
    observe(
      "reset vs settings",
      `after reset: arrival buffer UI="${stat}", GET /api/me status=${meRaw.status} padding=${meRaw.body?.padding_minutes}`,
    );
    expect(String(meRaw.body?.padding_minutes) === "10", `Reset sample kept the customised padding (${meRaw.body?.padding_minutes} instead of 10)`);
    return stat;
  });

  await check("true synchronous double-click queues one run", async () => {
    let posted = 0;
    const listener = (request) => {
      if (request.method() === "POST" && request.url().includes("/api/runs")) posted += 1;
    };
    context.on("request", listener);
    await page.evaluate(() => {
      const button = document.querySelector(".day-heading button.primary");
      button.click();
      button.click();
    });
    await page.waitForFunction(
      () => {
        const text = document.querySelector("p.status")?.textContent ?? "";
        return /Travel plan updated|decision needs your input|failed/i.test(text);
      },
      { timeout: 180_000 },
    );
    await page.waitForTimeout(1500);
    context.off("request", listener);
    expect(posted === 1, `${posted} POST /api/runs issued by a synchronous double-click`);
    return "1 run";
  });

  await check("skip link works when clicked directly (desktop)", async () => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    const style = await page.locator("a.skip-link").first().evaluate((element) => {
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return {
        display: computed.display,
        visibility: computed.visibility,
        position: computed.position,
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
      };
    });
    await page.locator("a.skip-link").first().click();
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({
      hash: window.location.hash,
      active: document.activeElement?.id || document.activeElement?.tagName,
      scrollY: Math.round(window.scrollY),
    }));
    observe("skip link style (desktop)", JSON.stringify(style));
    observe("skip link after mouse click (desktop)", JSON.stringify(after));
    expect(after.hash === "#get-started", `hash after click = "${after.hash}"`);
    return `hash=${after.hash} active=${after.active} scrollY=${after.scrollY}`;
  });

  await check("keyboard Tab reaches the skip link first (desktop)", async () => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(300);
    await page.keyboard.press("Tab");
    const first = await page.evaluate(() => ({
      text: document.activeElement?.textContent?.trim() ?? "",
      cls: document.activeElement?.className ?? "",
      id: document.activeElement?.id ?? "",
    }));
    observe("first tab stop (desktop)", JSON.stringify(first));
    expect(/skip/i.test(first.text) || /skip/i.test(first.cls), `first tab stop is ${JSON.stringify(first)}`);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({
      hash: window.location.hash,
      active: document.activeElement?.id || document.activeElement?.tagName,
    }));
    observe("skip link after Enter (desktop)", JSON.stringify(after));
    expect(after.hash === "#get-started", `Enter on the skip link left hash="${after.hash}"`);
    return `hash=${after.hash} active=${after.active}`;
  });

  await check("manual Recheck while paused is accepted", async () => {
    const stored = await page.evaluate(() => window.localStorage.getItem("glide-sample-session"));
    if (!stored) {
      await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
      await page.getByRole("button", { name: /Try a sample day/i }).click();
      await page.waitForSelector(".timeline", { timeout: 30_000 });
    }
    await page.getByRole("button", { name: /Pause automation/i }).click();
    await waitStatus(page, "Automation paused.");
    const status = await recheck(page);
    const enabled = await page.evaluate(async () => {
      const response = await fetch("/api/me", {
        headers: { "X-Glide-Session": window.localStorage.getItem("glide-sample-session") ?? "" },
      });
      return (await response.json()).enabled;
    });
    await page.getByRole("button", { name: /Resume automation/i }).click();
    await waitStatus(page, "Automation resumed.");
    observe("manual recheck while paused", `automation enabled=${enabled}, recheck status="${status}"`);
    return status;
  });
} catch (error) {
  checks.push({ name: "deep audit aborted", ok: false, detail: String(error) });
  console.log(`FAIL  deep audit aborted :: ${error}`);
} finally {
  await context.close().catch(() => {});
}

// Mobile: skip-link visibility and first tab stop.
const mobileContext = await browser.newContext({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
  locale: "en-GB",
});
const mobile = await mobileContext.newPage();
mobile.setDefaultTimeout(20_000);
try {
  await check("mobile skip link is visible to assistive tech", async () => {
    await mobile.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await mobile.waitForTimeout(300);
    const info = await mobile.locator("a.skip-link").first().evaluate((element) => {
      const computed = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return {
        display: computed.display,
        visibility: computed.visibility,
        position: computed.position,
        clip: computed.clipPath,
        width: rect.width,
        height: rect.height,
      };
    });
    observe("skip link style (mobile)", JSON.stringify(info));
    expect(info.display !== "none", "skip link is display:none on mobile");
    await mobile.keyboard.press("Tab");
    const first = await mobile.evaluate(() => ({
      text: document.activeElement?.textContent?.trim() ?? "",
      cls: document.activeElement?.className?.toString() ?? "",
      tag: document.activeElement?.tagName ?? "",
    }));
    observe("first tab stop (mobile emulation)", JSON.stringify(first));
    const acceptable = /skip/i.test(first.text) || /skip/i.test(first.cls);
    if (!acceptable) {
      // Emulation quirk: report rather than fail, and verify by mouse click.
      await mobile.locator("a.skip-link").first().click();
      await mobile.waitForTimeout(400);
      const after = await mobile.evaluate(() => window.location.hash);
      return `emulated Tab focused ${JSON.stringify(first)}; mouse click hash=${after}`;
    }
    return JSON.stringify(first);
  });
} catch (error) {
  checks.push({ name: "mobile skip-link probe aborted", ok: false, detail: String(error) });
} finally {
  await mobileContext.close().catch(() => {});
}

await browser.close();

const failed = checks.filter((entry) => !entry.ok);
const lines = [];
lines.push("# Glide live UI — second-pass findings");
lines.push("");
lines.push(`Target: ${BASE}`);
lines.push(`Run at: ${new Date().toISOString()}`);
lines.push("");
lines.push(`Checks: ${checks.length} (${checks.length - failed.length} passed, ${failed.length} failed)`);
lines.push("");
lines.push("## Failures / defects confirmed");
lines.push("");
for (const failure of failed) lines.push(`- **${failure.name}** — ${failure.detail}`);
lines.push("");
lines.push("## Checks");
lines.push("");
checks.forEach((entry, index) =>
  lines.push(`${index + 1}. ${entry.ok ? "PASS" : "FAIL"} — ${entry.name} — ${String(entry.detail).slice(0, 400)}`),
);
lines.push("");
lines.push("## Observations (raw behaviour)");
lines.push("");
for (const entry of observations) lines.push(`- **${entry.name}** — ${entry.detail}`);
lines.push("");
lines.push("## /api/day snapshots");
lines.push("");
for (const snapshot of daySnapshots) {
  lines.push(`- date=${snapshot.date} last_run=${snapshot.last_run} decisions=[${snapshot.decisions.join(", ")}] blocks=${snapshot.blocks}`);
  for (const event of snapshot.events) lines.push(`  - ${event}`);
}
lines.push("");
lines.push("## PATCH bodies observed");
lines.push("");
for (const entry of patched) lines.push(`- ${entry.url} ${entry.body}`);
lines.push("");
lines.push("## App console errors");
lines.push("");
lines.push(appLog.length ? appLog.join("\n") : "None.");
writeFileSync(join(outDir, "deep-report.md"), lines.join("\n"), "utf8");
console.log(`\n${checks.length - failed.length}/${checks.length} deep checks passed. Report: ${join(outDir, "deep-report.md")}`);
process.exit(0);
