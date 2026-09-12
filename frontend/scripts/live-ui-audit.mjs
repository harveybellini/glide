/**
 * Live functional audit of the deployed Glide site.
 *
 * Drives a real Chromium instance against the live CloudFront URL and clicks
 * every control, recording pass/fail, console errors, failed requests and
 * screenshots. Run with: node scripts/live-ui-audit.mjs [url]
 *
 * Artifacts land in ../temp/functional-test/.
 */
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "..", "..", "temp", "functional-test");
const shotDir = join(outDir, "shots");
mkdirSync(shotDir, { recursive: true });

const BASE = (process.argv[2] ?? "https://d3tvxy281s2u11.cloudfront.net").replace(/\/$/, "");

const results = [];
const consoleLog = [];
const netLog = [];
let stepIndex = 0;

function stamp() {
  return String(stepIndex).padStart(2, "0");
}

async function shot(page, name) {
  stepIndex += 1;
  const file = join(shotDir, `${stamp()}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true }).catch((error) => {
    consoleLog.push({ type: "shot-error", text: `${name}: ${error.message}` });
  });
  return file;
}

async function aria(page, name) {
  try {
    const snapshot = await page.locator("body").ariaSnapshot();
    writeFileSync(join(shotDir, `${stamp()}-${name}.aria.md`), snapshot, "utf8");
  } catch (error) {
    consoleLog.push({ type: "aria-error", text: `${name}: ${error.message}` });
  }
}

async function check(name, fn) {
  const started = Date.now();
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail ?? "", ms: Date.now() - started });
    console.log(`PASS  ${name}${detail ? ` :: ${detail}` : ""}`);
    return true;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    results.push({ name, ok: false, detail: message, ms: Date.now() - started });
    console.log(`FAIL  ${name} :: ${message}`);
    return false;
  }
}

function expect(condition, message) {
  if (!condition) throw new Error(message);
}

async function bodyText(page) {
  return (await page.locator("body").innerText()).replace(/\s+/g, " ").trim();
}

async function waitForStatus(page, fragment, timeout = 180_000) {
  await page.waitForFunction(
    (needle) => (document.querySelector("p.status")?.textContent ?? "").includes(needle),
    fragment,
    { timeout },
  );
}

async function clickAndSettle(page, locator) {
  await locator.click();
  await page.waitForTimeout(250);
}

function attachTelemetry(context) {
  context.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      consoleLog.push({ type: message.type(), text: message.text().slice(0, 500) });
    }
  });
  context.on("pageerror", (error) => {
    consoleLog.push({ type: "pageerror", text: String(error).slice(0, 500) });
  });
  context.on("requestfailed", (request) => {
    netLog.push({
      kind: "requestfailed",
      url: request.url(),
      method: request.method(),
      failure: request.failure()?.errorText ?? "unknown",
    });
  });
  context.on("response", async (response) => {
    const url = response.url();
    if (!url.includes("/api/")) return;
    const entry = {
      kind: "response",
      url: url.replace(BASE, ""),
      method: response.request().method(),
      status: response.status(),
    };
    if (response.status() >= 400) {
      entry.body = await response.text().catch(() => "");
    }
    netLog.push(entry);
  });
}

function countRequests(predicate) {
  return netLog.filter(predicate).length;
}

async function runJourney(page, label) {
  const prefix = `${label} `;

  await check(`${prefix}landing renders`, async () => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.getByRole("button", { name: /Try a sample day/i }).waitFor({ timeout: 20_000 });
    const text = await bodyText(page);
    expect(/Life happens/i.test(text), "hero headline missing");
    expect(/A calendar that connects the dots/i.test(text), "how-it-works section missing");
    return (await page.title()) || "no title";
  });
  await aria(page, `${label}-landing`);
  await shot(page, `${label}-01-landing`);

  await check(`${prefix}document metadata`, async () => {
    const info = await page.evaluate(() => ({
      lang: document.documentElement.lang,
      title: document.title,
      description:
        document.querySelector('meta[name="description"]')?.getAttribute("content") ?? null,
      viewport: document.querySelector('meta[name="viewport"]')?.getAttribute("content") ?? null,
    }));
    expect(info.lang && info.lang.length > 0, "<html lang> missing");
    expect(info.title.trim().length > 0, "empty <title>");
    expect(info.viewport, "no viewport meta");
    return JSON.stringify(info);
  });

  await check(`${prefix}favicon responds`, async () => {
    const response = await page.request.get(`${BASE}/favicon.ico`);
    expect(response.status() < 400, `favicon.ico -> ${response.status()}`);
    return `HTTP ${response.status()}`;
  });

  await check(`${prefix}Google connect link is wired`, async () => {
    const href = await page
      .getByRole("link", { name: /Connect Google Calendar/i })
      .first()
      .getAttribute("href");
    expect(href === "/api/auth/google/start", `unexpected href ${href}`);
    return href;
  });

  await check(`${prefix}"How it works" anchor scrolls`, async () => {
    await clickAndSettle(page, page.getByRole("link", { name: /How it works/i }));
    const hash = await page.evaluate(() => window.location.hash);
    const visible = await page
      .getByRole("heading", { name: /A calendar that connects the dots/i })
      .isVisible();
    expect(hash === "#how-it-works", `hash is ${hash}`);
    expect(visible, "how-it-works heading not visible after click");
    return `hash=${hash}`;
  });

  await check(`${prefix}skip link moves focus`, async () => {
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => ({
      text: document.activeElement?.textContent?.trim() ?? "",
      href: document.activeElement?.getAttribute("href") ?? "",
    }));
    expect(/skip/i.test(focused.text), `first tab stop is "${focused.text}"`);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    const hash = await page.evaluate(() => window.location.hash);
    expect(hash === "#get-started", `skip link jumped to ${hash}`);
    return `${focused.text} -> ${hash}`;
  });

  const runsBefore = countRequests(
    (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
  );
  await check(`${prefix}start sample day`, async () => {
    await clickAndSettle(page, page.getByRole("button", { name: /Try a sample day/i }));
    await page.waitForSelector(".timeline", { timeout: 30_000 });
    const stored = await page.evaluate(() => localStorage.getItem("glide-sample-session"));
    expect(Boolean(stored), "sample session id not persisted to localStorage");
    const text = await bodyText(page);
    expect(/Sample workspace/i.test(text), "mode badge missing");
    expect(/Client visit/i.test(text), "appointment titles missing");
    expect(/Glide is on/i.test(text), "automation state missing");
    const stats = await page.locator(".day-stats div").allInnerTexts();
    return `stats=[${stats.map((s) => s.replace(/\s+/g, " ")).join(" | ")}] session=${stored?.slice(0, 8)}…`;
  });
  await aria(page, `${label}-day`);
  await shot(page, `${label}-02-sample-day`);

  await check(`${prefix}day stats and timeline wiring`, async () => {
    const stats = (await page.locator(".day-stats .stat-value").allInnerTexts()).map((s) =>
      s.replace(/\s+/g, " ").trim(),
    );
    expect(stats.length === 4, `expected 4 stat cards, found ${stats.length}`);
    const appointments = await page.locator("article.row.event").count();
    const blocks = await page.locator("article.row.travel").count();
    expect(appointments >= 1, "no appointments rendered");
    expect(
      Number(stats[0]) === appointments,
      `stat says ${stats[0]} appointments but ${appointments} rows rendered`,
    );
    expect(
      Number(stats[1]) === blocks,
      `stat says ${stats[1]} travel blocks but ${blocks} rows rendered`,
    );
    return `appointments=${appointments} travelBlocks=${blocks} stats=${stats.join(",")}`;
  });

  const titleBefore = await check(`${prefix}activity section present`, async () => {
    const text = await page.locator("#activity").innerText();
    return text.replace(/\s+/g, " ").trim();
  });

  await check(`${prefix}nav anchor to Activity`, async () => {
    await clickAndSettle(page, page.getByRole("link", { name: /Activity/i }).first());
    const inView = await page.evaluate(() => {
      const el = document.getElementById("activity");
      if (!el) return false;
      const rect = el.getBoundingClientRect();
      return rect.top < window.innerHeight && rect.bottom > 0;
    });
    expect(inView, "activity section not scrolled into view");
    return "in viewport";
  });

  await check(`${prefix}Recheck now runs the planner`, async () => {
    const runsBeforeClick = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    const started = Date.now();
    await page.getByRole("button", { name: /Recheck now|Checking/i }).click();
    const busyLabel = await page
      .locator(".day-heading button.primary")
      .innerText()
      .catch(() => "");
    const disabled = await page.locator(".day-heading button.primary").isDisabled();
    await page.waitForFunction(
      () => {
        const text = document.querySelector("p.status")?.textContent ?? "";
        return /Travel plan updated|decision needs your input|Check failed|could not be completed/i.test(
          text,
        );
      },
      { timeout: 180_000 },
    );
    const status = (await page.locator("p.status").innerText()).trim();
    const elapsed = Date.now() - started;
    const runsAfter = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    expect(runsAfter === runsBeforeClick + 1, `expected 1 run POST, saw ${runsAfter - runsBeforeClick}`);
    expect(!/failed|could not be completed/i.test(status), `run failed: ${status}`);
    return `status="${status}" elapsed=${elapsed}ms busyLabel="${busyLabel.trim()}" disabledDuringRun=${disabled}`;
  });
  await shot(page, `${label}-03-after-recheck`);
  await aria(page, `${label}-after-recheck`);

  await check(`${prefix}planner produced travel blocks + receipts`, async () => {
    const blocks = await page.locator("article.row.travel").count();
    const receipts = await page.locator("#activity li").count();
    const activityText = (await page.locator("#activity").innerText()).replace(/\s+/g, " ");
    const lastRun = await page.locator("p.last-run").innerText().catch(() => "");
    expect(blocks >= 1, `expected at least one travel block, found ${blocks}`);
    expect(receipts >= 1, `expected receipts, found ${receipts}`);
    expect(!/failed/i.test(lastRun), `last run reported ${lastRun}`);
    return `blocks=${blocks} receipts=${receipts} lastRun="${lastRun.replace(/\s+/g, " ")}" activity="${activityText.slice(0, 140)}"`;
  });

  await check(`${prefix}day stats match rendered rows after recheck`, async () => {
    const stats = (await page.locator(".day-stats .stat-value").allInnerTexts()).map((s) =>
      s.replace(/\s+/g, " ").trim(),
    );
    const appointments = await page.locator("article.row.event").count();
    const blocks = await page.locator("article.row.travel").count();
    const decisions = await page.locator("article.decision").count();
    expect(Number(stats[0]) === appointments, `appointments stat ${stats[0]} != rows ${appointments}`);
    expect(Number(stats[1]) === blocks, `travel stat ${stats[1]} != rows ${blocks}`);
    expect(Number(stats[3]) === decisions, `decisions stat ${stats[3]} != cards ${decisions}`);
    return `appointments=${appointments} blocks=${blocks} decisions=${decisions}`;
  });

  await check(`${prefix}no console errors on the happy path`, async () => {
    const errors = consoleLog.filter((entry) =>
      ["error", "pageerror"].includes(entry.type),
    );
    expect(errors.length === 0, `${errors.length} console errors: ${JSON.stringify(errors.slice(0, 3))}`);
    return "clean";
  });

  return { titleBefore };
}

async function decisionPhase(page) {
  const decisionCount = await page.locator("article.decision").count();
  if (decisionCount === 0) {
    results.push({
      name: "decision card available to exercise",
      ok: false,
      detail: "canonical check produced no decision card; decision controls unexercised",
      ms: 0,
    });
    console.log("FAIL  decision card available to exercise :: none rendered");
    return;
  }
  await check("decision card exposes explanation and actions", async () => {
    const card = page.locator("article.decision").first();
    const text = (await card.innerText()).replace(/\s+/g, " ");
    const buttons = (await card.locator("button").allInnerTexts()).map((b) => b.trim());
    expect(text.length > 0, "empty decision card");
    expect(buttons.length >= 1, "decision card has no action buttons");
    return `text="${text.slice(0, 160)}" buttons=[${buttons.join(", ")}]`;
  });
  await shot(page, "04-decision-card");

  await check('"Edit appointments" scrolls to timeline or opens Google Calendar', async () => {
    const editButton = page.getByRole("button", { name: /^Edit appointments$/i });
    const link = page.getByRole("link", { name: /Edit in Google Calendar/i });
    if (await editButton.count()) {
      await clickAndSettle(page, editButton.first());
      const inView = await page.evaluate(() => {
        const rect = document.getElementById("timeline")?.getBoundingClientRect();
        return rect ? rect.top < window.innerHeight : false;
      });
      expect(inView, "timeline not scrolled into view");
      return "sample mode: scrolled to timeline";
    }
    expect((await link.count()) > 0, "neither edit control present");
    return "live mode: Google Calendar link present";
  });

  await check('"Keep my edit" resolves the decision', async () => {
    const button = page.getByRole("button", { name: /Keep my edit/i });
    expect((await button.count()) > 0, "action not offered for this decision");
    const before = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/resolve"),
    );
    await button.first().click();
    await waitForStatus(page, "Decision saved.", 120_000);
    const after = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/resolve"),
    );
    expect(after === before + 1, `expected 1 resolve POST, saw ${after - before}`);
    const cards = await page.locator("article.decision").count();
    return `resolve POSTs=${after - before} cardsRemaining=${cards}`;
  });
  await shot(page, "05-after-keep-my-edit");
}

async function editorPhase(page) {
  await check("event editor opens on Edit", async () => {
    const edit = page.getByRole("button", { name: "Edit", exact: true }).first();
    expect((await edit.count()) > 0, "no Edit buttons on sample events");
    await clickAndSettle(page, edit);
    const editor = page.locator("form.event-editor");
    await editor.waitFor({ timeout: 10_000 });
    const label = await editor.getAttribute("aria-label");
    const expanded = await edit.getAttribute("aria-expanded");
    expect(expanded === "true", `aria-expanded=${expanded}`);
    return `editor="${label}"`;
  });
  await shot(page, "06-event-editor");

  await check("event editor rejects end <= start without a request", async () => {
    const editor = page.locator("form.event-editor");
    const patchBefore = countRequests((entry) => entry.method === "PATCH");
    await editor.getByLabel("End").fill("08:00");
    await editor.getByRole("button", { name: /Save changes/i }).click();
    await page.waitForTimeout(500);
    const error = await editor.locator("p.error").innerText().catch(() => "");
    const patchAfter = countRequests((entry) => entry.method === "PATCH");
    expect(/End time must be after start time/i.test(error), `error text: "${error}"`);
    expect(patchAfter === patchBefore, "invalid save still issued a PATCH");
    return error.trim();
  });

  await check("Escape closes the editor and restores focus", async () => {
    await page.locator("form.event-editor input[type=time]").first().focus();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const open = await page.locator("form.event-editor").count();
    const focusText = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    expect(open === 0, "editor still open after Escape");
    expect(focusText === "Edit", `focus moved to "${focusText}"`);
    return `closed, focus="${focusText}"`;
  });

  await check("valid edit saves and refetches the day", async () => {
    const edit = page.getByRole("button", { name: "Edit", exact: true }).first();
    await clickAndSettle(page, edit);
    const editor = page.locator("form.event-editor");
    await editor.waitFor({ timeout: 10_000 });
    const patchBefore = countRequests((entry) => entry.method === "PATCH");
    await editor.getByLabel("Start").fill("09:30");
    await editor.getByLabel("End").fill("10:30");
    await editor.getByRole("button", { name: /Save changes/i }).click();
    await waitForStatus(page, "Appointment updated", 30_000);
    const patchAfter = countRequests((entry) => entry.method === "PATCH");
    const open = await page.locator("form.event-editor").count();
    const timeline = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
    expect(patchAfter === patchBefore + 1, `PATCH count ${patchAfter - patchBefore}`);
    expect(open === 0, "editor left open after save");
    expect(/09:30/.test(timeline), "timeline does not show the new time");
    return `PATCH ok, timeline contains 09:30`;
  });
  await shot(page, "07-after-edit");
  return true;
}

async function settingsPhase(page) {
  await check("settings panel opens with all controls", async () => {
    await clickAndSettle(page, page.getByRole("button", { name: /Settings/i }).first());
    const panel = page.locator("#travel-settings");
    await panel.waitFor({ timeout: 10_000 });
    const fields = await panel.locator("input, select, button").count();
    const expanded = await page
      .getByRole("button", { name: /Settings/i })
      .first()
      .getAttribute("aria-expanded");
    expect(expanded === "true", `aria-expanded=${expanded}`);
    expect(fields >= 5, `only ${fields} controls in settings`);
    return `controls=${fields}`;
  });
  await shot(page, "08-settings-panel");

  await check("settings rejects an out-of-range arrival buffer", async () => {
    const panel = page.locator("#travel-settings");
    const patchBefore = countRequests((entry) => entry.method === "PATCH");
    await panel.getByLabel(/Arrival buffer/i).fill("61");
    await panel.getByRole("button", { name: /Save settings/i }).click();
    await page.waitForTimeout(400);
    const error = await panel.locator("p.error").innerText().catch(() => "");
    const patchAfter = countRequests((entry) => entry.method === "PATCH");
    expect(/between 0 and 60/i.test(error), `error text: "${error}"`);
    expect(patchAfter === patchBefore, "invalid settings still issued a PATCH");
    return error.trim();
  });

  await check("empty arrival buffer is not silently coerced", async () => {
    const panel = page.locator("#travel-settings");
    const padding = panel.getByLabel(/Arrival buffer/i);
    await padding.fill("");
    await panel.getByRole("button", { name: /Save settings/i }).click();
    await page.waitForTimeout(600);
    const stillOpen = await panel.count();
    const error = await panel.locator("p.error").innerText().catch(() => "");
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).trim();
    if (stillOpen === 0) {
      throw new Error(`empty buffer saved silently; arrival buffer now ${stat}`);
    }
    expect(error.length > 0, "no error shown for empty buffer");
    return `blocked with "${error.trim()}"`;
  });

  await check("valid settings save, close and update the day stats", async () => {
    const panel = page.locator("#travel-settings");
    await panel.getByLabel(/Arrival buffer/i).fill("15");
    await panel.getByLabel(/Earliest departure/i).fill("06:30");
    await panel.getByLabel(/Start address/i).selectOption("place_b");
    await panel.getByLabel(/Time zone/i).selectOption("Europe/Paris");
    await panel.getByRole("button", { name: /Save settings/i }).click();
    await waitForStatus(page, "Settings saved.", 30_000);
    const open = await page.locator("#travel-settings").count();
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).replace(/\s+/g, "");
    const noteFooter = (await page.locator(".note-footer").innerText()).trim();
    expect(open === 0, "settings panel still open after save");
    expect(stat.startsWith("15"), `arrival buffer stat is ${stat}`);
    expect(/Westfield/i.test(noteFooter), `journey note footer shows "${noteFooter}"`);
    return `buffer=${stat} startPlace="${noteFooter}"`;
  });
  await shot(page, "09-after-settings-save");

  await check("settings survive a reload", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".timeline", { timeout: 30_000 });
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).replace(/\s+/g, "");
    const noteFooter = (await page.locator(".note-footer").innerText()).trim();
    expect(stat.startsWith("15"), `after reload buffer stat is ${stat}`);
    expect(/Westfield/i.test(noteFooter), `after reload start place is "${noteFooter}"`);
    return `buffer=${stat}`;
  });

  await check("Cancel discards unsaved settings", async () => {
    await clickAndSettle(page, page.getByRole("button", { name: /Settings/i }).first());
    const panel = page.locator("#travel-settings");
    await panel.waitFor({ timeout: 10_000 });
    await panel.getByLabel(/Arrival buffer/i).fill("45");
    await panel.getByRole("button", { name: /Cancel/i }).click();
    await page.waitForTimeout(300);
    const open = await page.locator("#travel-settings").count();
    const stat = (await page.locator(".day-stats .stat-value").nth(2).innerText()).replace(/\s+/g, "");
    await clickAndSettle(page, page.getByRole("button", { name: /Settings/i }).first());
    const value = await page.locator("#travel-settings").getByLabel(/Arrival buffer/i).inputValue();
    await page.keyboard.press("Escape");
    expect(open === 0, "panel stayed open after Cancel");
    expect(stat.startsWith("15"), `day stat changed to ${stat} after Cancel`);
    expect(value === "15", `reopened panel shows ${value} instead of the saved 15`);
    return "discarded";
  });

  await check("Escape closes settings and restores focus to the toggle", async () => {
    const toggle = page.getByRole("button", { name: /Settings/i }).first();
    await clickAndSettle(page, toggle);
    await page.locator("#travel-settings").waitFor({ timeout: 10_000 });
    await page.locator("#travel-settings input").first().focus();
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    const open = await page.locator("#travel-settings").count();
    const focusText = await page.evaluate(() => document.activeElement?.textContent?.trim() ?? "");
    expect(open === 0, "panel still open after Escape");
    expect(/Settings/i.test(focusText), `focus went to "${focusText}"`);
    return `closed, focus="${focusText}"`;
  });
}

async function automationPhase(page) {
  await check("pause automation updates the sidebar and status", async () => {
    await clickAndSettle(page, page.getByRole("button", { name: /Pause automation/i }));
    await waitForStatus(page, "Automation paused.", 30_000);
    const text = await bodyText(page);
    expect(/Glide is paused/i.test(text), "sidebar did not switch to paused");
    expect(/Resume automation/i.test(text), "resume button missing");
    return "paused";
  });
  await shot(page, "10-paused");

  await check("paused state survives a reload", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForSelector(".timeline", { timeout: 30_000 });
    const text = await bodyText(page);
    expect(/Glide is paused/i.test(text), "pause was not persisted server-side");
    return "persisted";
  });

  await check("paused state blocks new runs", async () => {
    const runsBefore = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    await page.getByRole("button", { name: /Recheck now/i }).click();
    await page.waitForTimeout(2500);
    const runsAfter = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    const status = (await page.locator("p.status").innerText()).trim();
    const error = await page.locator("p.error").innerText().catch(() => "");
    const blocks = await page.locator("article.row.travel").count();
    return `run POSTs=${runsAfter - runsBefore} status="${status}" error="${error.trim()}" blocks=${blocks}`;
  });

  await check("resume automation restores the sidebar", async () => {
    await clickAndSettle(page, page.getByRole("button", { name: /Resume automation/i }));
    await waitForStatus(page, "Automation resumed.", 30_000);
    const text = await bodyText(page);
    expect(/Glide is on/i.test(text), "sidebar did not switch back to on");
    return "resumed";
  });

  await check("recheck works again after resume", async () => {
    await page.getByRole("button", { name: /Recheck now/i }).click();
    await page.waitForFunction(
      () => {
        const text = document.querySelector("p.status")?.textContent ?? "";
        return /Travel plan updated|decision needs your input|failed/i.test(text);
      },
      { timeout: 180_000 },
    );
    const status = (await page.locator("p.status").innerText()).trim();
    expect(!/failed/i.test(status), `run failed: ${status}`);
    return status;
  });
}

async function resetPhase(page) {
  await check("reset sample returns the canonical day", async () => {
    const statsBefore = (await page.locator(".day-stats .stat-value").allInnerTexts()).map((s) =>
      s.replace(/\s+/g, " ").trim(),
    );
    await clickAndSettle(page, page.getByRole("button", { name: /Reset sample/i }));
    await waitForStatus(page, "Sample reset", 30_000);
    await page.waitForTimeout(500);
    const stats = (await page.locator(".day-stats .stat-value").allInnerTexts()).map((s) =>
      s.replace(/\s+/g, " ").trim(),
    );
    const text = await bodyText(page);
    const buffer = stats[2];
    const noteFooter = (await page.locator(".note-footer").innerText()).trim();
    expect(buffer.startsWith("10"), `arrival buffer not restored to 10 (got ${buffer})`);
    expect(/Northside/i.test(noteFooter), `start place not restored (got "${noteFooter}")`);
    expect(/Glide is on/i.test(text), "automation not re-enabled by reset");
    return `before=[${statsBefore.join(",")}] after=[${stats.join(",")}]`;
  });
  await shot(page, "11-after-reset");
}

async function deepLinkPhase(page) {
  await check("?decision=<id> focuses and cleans the URL", async () => {
    await page.getByRole("button", { name: /Recheck now/i }).click();
    await page.waitForFunction(
      () => {
        const text = document.querySelector("p.status")?.textContent ?? "";
        return /Travel plan updated|decision needs your input|failed/i.test(text);
      },
      { timeout: 180_000 },
    );
    const cards = await page.locator("article.decision").count();
    if (cards === 0) throw new Error("no decision available to deep-link to");
    const id = await page.locator("article.decision").first().getAttribute("id");
    const decisionId = id.replace("decision-", "");
    await page.goto(`${BASE}/?decision=${decisionId}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector(`#decision-${decisionId}`, { timeout: 60_000 });
    await page.waitForTimeout(1200);
    const focused = await page.locator(`#decision-${decisionId}`).getAttribute("class");
    const url = page.url();
    const inView = await page.evaluate((target) => {
      const rect = document.getElementById(`decision-${target}`)?.getBoundingClientRect();
      return rect ? rect.top < window.innerHeight && rect.bottom > 0 : false;
    }, decisionId);
    expect(/decision-focused/.test(focused ?? ""), `class=${focused}`);
    expect(!url.includes("decision="), `query param not cleaned: ${url}`);
    expect(inView, "focused decision not scrolled into view");
    return `id=${decisionId.slice(0, 12)}… focused+scrolled`;
  });
  await shot(page, "12-deep-link");
}

async function miscPhase(page) {
  await check("refresh keeps the sample session (no landing page)", async () => {
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const text = await bodyText(page);
    expect(!/Try a sample day/i.test(text), "landing page reappeared after refresh");
    expect(/Recheck now/i.test(text), "day view missing after refresh");
    return "day view restored";
  });

  await check("rapid double-click on Recheck queues one run", async () => {
    const runsBefore = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    const button = page.getByRole("button", { name: /Recheck now/i });
    await button.click();
    await button.click({ force: true, timeout: 5000 }).catch(() => "second click blocked");
    await page.waitForFunction(
      () => {
        const text = document.querySelector("p.status")?.textContent ?? "";
        return /Travel plan updated|decision needs your input|failed/i.test(text);
      },
      { timeout: 180_000 },
    );
    const runsAfter = countRequests(
      (entry) => entry.method === "POST" && entry.url.includes("/api/runs"),
    );
    expect(runsAfter - runsBefore === 1, `${runsAfter - runsBefore} run POSTs issued`);
    return "1 run";
  });

  await check("signing out clears sample state back to the landing page", async () => {
    await page.evaluate(() => window.localStorage.clear());
    await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const text = await bodyText(page);
    expect(/Try a sample day/i.test(text), "landing page did not return after clearing state");
    return "landing";
  });

  await check("unknown query param is ignored", async () => {
    await page.goto(`${BASE}/?decision=not-a-real-id`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(800);
    const text = await bodyText(page);
    expect(/Try a sample day/i.test(text), "landing page missing for unknown decision id");
    return "landing rendered";
  });

  await check("no failed or 5xx network requests during the audit", async () => {
    const failures = netLog.filter(
      (entry) =>
        entry.kind === "requestfailed" ||
        (entry.kind === "response" && entry.status >= 500),
    );
    expect(
      failures.length === 0,
      `${failures.length} network failures: ${JSON.stringify(failures.slice(0, 4))}`,
    );
    const fourX = netLog.filter(
      (entry) => entry.kind === "response" && entry.status >= 400 && entry.status < 500,
    );
    return `0 failures, ${fourX.length} 4xx responses`;
  });
}

function buildReport() {
  const passed = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok);
  const apiCalls = netLog.filter((entry) => entry.kind === "response");
  const slowest = [...apiCalls].sort((a, b) => (b.status ?? 0) - (a.status ?? 0)).slice(0, 0);
  const lines = [];
  lines.push("# Live UI audit — Glide");
  lines.push("");
  lines.push(`Target: ${BASE}`);
  lines.push(`Run at: ${new Date().toISOString()}`);
  lines.push("");
  lines.push(`## Summary`);
  lines.push("");
  lines.push(`- Checks: ${results.length} (${passed} passed, ${failed.length} failed)`);
  lines.push(`- API calls observed: ${apiCalls.length}`);
  lines.push(`- Console errors/warnings: ${consoleLog.length}`);
  lines.push(`- Network failures: ${netLog.filter((e) => e.kind === "requestfailed").length}`);
  lines.push("");
  lines.push("## Failures");
  lines.push("");
  if (failed.length === 0) lines.push("None.");
  for (const failure of failed) {
    lines.push(`- **${failure.name}** — ${failure.detail}`);
  }
  lines.push("");
  lines.push("## All checks");
  lines.push("");
  lines.push("| # | Check | Result | Detail |");
  lines.push("|---|-------|--------|--------|");
  results.forEach((result, index) => {
    lines.push(
      `| ${index + 1} | ${result.name} | ${result.ok ? "PASS" : "FAIL"} | ${String(result.detail).replace(/\|/g, "\\|").slice(0, 300)} |`,
    );
  });
  lines.push("");
  lines.push("## Console log");
  lines.push("");
  if (consoleLog.length === 0) lines.push("No errors or warnings.");
  for (const entry of consoleLog.slice(0, 60)) {
    lines.push(`- [${entry.type}] ${entry.text}`);
  }
  lines.push("");
  lines.push("## Network log (API responses)");
  lines.push("");
  lines.push("| Method | Path | Status |");
  lines.push("|--------|------|--------|");
  const seen = new Map();
  for (const entry of apiCalls) {
    const key = `${entry.method} ${entry.url} ${entry.status}`;
    seen.set(key, (seen.get(key) ?? 0) + 1);
  }
  for (const [key, count] of seen) {
    const [method, ...rest] = key.split(" ");
    const status = rest.pop();
    lines.push(`| ${method} | ${rest.join(" ")} | ${status}${count > 1 ? ` (x${count})` : ""} |`);
  }
  if (slowest.length) lines.push("");
  writeFileSync(join(outDir, "ui-audit-report.md"), lines.join("\n"), "utf8");
  writeFileSync(
    join(outDir, "ui-audit-raw.json"),
    JSON.stringify({ base: BASE, results, consoleLog, netLog }, null, 2),
    "utf8",
  );
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: "en-GB",
  timezoneId: "Europe/London",
});
attachTelemetry(context);
const page = await context.newPage();
page.setDefaultTimeout(20_000);

try {
  await runJourney(page, "desktop");
  await decisionPhase(page);
  await editorPhase(page);
  await settingsPhase(page);
  await automationPhase(page);
  await resetPhase(page);
  await deepLinkPhase(page);
  await miscPhase(page);
  await check("desktop viewport has no horizontal overflow", async () => {
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow <= 2, `horizontal overflow of ${overflow}px`);
    return "none";
  });
} catch (error) {
  results.push({
    name: "audit aborted",
    ok: false,
    detail: error instanceof Error ? error.message : String(error),
    ms: 0,
  });
} finally {
  await context.close().catch(() => {});
}

// Mobile pass: fresh session, core journey plus settings and tap-target check.
const mobileContext = await browser.newContext({
  viewport: { width: 390, height: 844 },
  isMobile: true,
  hasTouch: true,
  deviceScaleFactor: 3,
  locale: "en-GB",
  timezoneId: "Europe/London",
});
attachTelemetry(mobileContext);
const mobile = await mobileContext.newPage();
mobile.setDefaultTimeout(20_000);
try {
  await runJourney(mobile, "mobile");
  await check("mobile viewport has no horizontal overflow", async () => {
    const overflow = await mobile.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow <= 2, `horizontal overflow of ${overflow}px`);
    const wide = await mobile.evaluate(() =>
      [...document.querySelectorAll("body *")]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 2)
        .slice(0, 5)
        .map((el) => `${el.tagName.toLowerCase()}.${el.className}`.slice(0, 60)),
    );
    return wide.length ? `overflowing: ${wide.join(", ")}` : "none";
  });
  await check("mobile settings panel opens above the fold", async () => {
    await clickAndSettle(mobile, mobile.getByRole("button", { name: /Settings/i }).first());
    const visible = await mobile.locator("#travel-settings").isVisible();
    expect(visible, "settings panel not visible on mobile");
    const box = await mobile.locator("#travel-settings").boundingBox();
    expect(box && box.width > 200, `panel width ${box?.width}`);
    return `panel ${Math.round(box.width)}x${Math.round(box.height)}`;
  });
  await shot(mobile, "mobile-13-settings");
  await check("mobile tap targets are at least 24px", async () => {
    const small = await mobile.evaluate(() =>
      [...document.querySelectorAll("button, a")]
        .filter((el) => {
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0 && (rect.height < 24 || rect.width < 24);
        })
        .slice(0, 8)
        .map((el) => `${el.tagName.toLowerCase()} "${(el.textContent ?? "").trim().slice(0, 24)}" ${Math.round(el.getBoundingClientRect().width)}x${Math.round(el.getBoundingClientRect().height)}`),
    );
    expect(small.length === 0, `small targets: ${small.join("; ")}`);
    return "all >= 24px";
  });
} catch (error) {
  results.push({
    name: "mobile pass aborted",
    ok: false,
    detail: error instanceof Error ? error.message : String(error),
    ms: 0,
  });
} finally {
  await mobileContext.close().catch(() => {});
}

await browser.close();
buildReport();
const failedCount = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failedCount}/${results.length} checks passed. Report: ${join(outDir, "ui-audit-report.md")}`);
process.exit(0);
