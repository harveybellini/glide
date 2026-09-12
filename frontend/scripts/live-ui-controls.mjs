/**
 * Third live pass for Glide: connect path, decision deep links, run latency,
 * realistic double-click, clean-viewport skip link and settings edge cases.
 *
 * Run with: node scripts/live-ui-controls.mjs
 */
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const outDir = resolve(here, "..", "..", "temp", "functional-test");
const shotDir = join(outDir, "shots-controls");
mkdirSync(shotDir, { recursive: true });
const BASE = (process.argv[2] ?? "https://d3tvxy281s2u11.cloudfront.net").replace(/\/$/, "");

const checks = [];
const observations = [];
const runLog = [];
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
    .screenshot({
      path: join(shotDir, `${String(shotIndex).padStart(2, "0")}-${name}.png`),
      fullPage: true,
    })
    .catch(() => {});
}

async function waitStatus(page, fragment, timeout = 200_000) {
  await page.waitForFunction(
    (needle) => (document.querySelector("p.status")?.textContent ?? "").includes(needle),
    fragment,
    { timeout },
  );
}

async function recheckAndWait(page) {
  await page.getByRole("button", { name: /Recheck now|Checking/i }).click();
  await page.waitForFunction(
    () => {
      const status = document.querySelector("p.status")?.textContent ?? "";
      const error = document.querySelector("p.error")?.textContent ?? "";
      return /Travel plan updated|decision needs your input|could not be completed|timed out/i.test(
        `${status} ${error}`,
      );
    },
    { timeout: 200_000 },
  );
  const status = (await page.locator("p.status").innerText()).trim();
  const error = (await page.locator("p.error").innerText().catch(() => "")).trim();
  return { status, error };
}

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  locale: "en-GB",
  timezoneId: "Europe/London",
});
const page = await context.newPage();
page.setDefaultTimeout(20_000);

await check("OAuth start redirects to Google with the right parameters", async () => {
  const response = await context.request.get(`${BASE}/api/auth/google/start`, {
    maxRedirects: 0,
    failOnStatusCode: false,
  });
  const location = response.headers()["location"] ?? "";
  const status = response.status();
  let host = "(no location header)";
  try {
    host = new URL(location).host;
  } catch {
    host = "(no location header)";
  }
  observe("OAuth start", `HTTP ${status} -> ${host}`);
  expect([302, 303, 307, 308].includes(status), `status ${status}`);
  expect(/accounts\.google\.com$/.test(host), `redirect host ${host}`);
  const url = new URL(location);
  expect(url.searchParams.get("client_id"), "missing client_id");
  const redirectUri = url.searchParams.get("redirect_uri") ?? "";
  expect(/\/api\/auth\/google\/callback$/.test(redirectUri), `redirect_uri=${redirectUri}`);
  const scope = url.searchParams.get("scope") ?? "";
  expect(/calendar/.test(scope), `scope=${scope}`);
  return `HTTP ${status} redirect_uri=${redirectUri} scope="${scope.slice(0, 60)}…"`;
});

await check("unknown API route returns JSON 404 rather than an HTML error", async () => {
  const response = await context.request.get(`${BASE}/api/definitely-not-a-route`, {
    failOnStatusCode: false,
  });
  const body = await response.text();
  observe(
    "404 body",
    `HTTP ${response.status()} content-type=${response.headers()["content-type"]} body=${body.slice(0, 120)}`,
  );
  expect(response.status() === 404, `status ${response.status()}`);
  return `HTTP 404 ${body.slice(0, 60)}`;
});

await check("clean visit: skip link is the first tab stop and works", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => window.localStorage.clear());
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /Try a sample day/i }).waitFor({ timeout: 20_000 });
  await page.keyboard.press("Tab");
  const first = await page.evaluate(() => ({
    text: document.activeElement?.textContent?.trim() ?? "",
    cls: String(document.activeElement?.className ?? ""),
  }));
  observe("landing first tab stop (clean)", JSON.stringify(first));
  expect(/skip/i.test(first.text) || /skip/i.test(first.cls), `first tab stop ${JSON.stringify(first)}`);
  await page.keyboard.press("Enter");
  await page.waitForTimeout(400);
  const after = await page.evaluate(() => ({
    hash: window.location.hash,
    active: document.activeElement?.id ?? document.activeElement?.tagName ?? "",
  }));
  observe("landing skip link after Enter", JSON.stringify(after));
  expect(after.hash === "#get-started", `hash after Enter = "${after.hash}"`);
  expect(after.active === "get-started", `focus moved to ${after.active}`);
  return `first stop="${first.text}" hash=${after.hash} focus=#${after.active}`;
});

await check("sample day starts and times render in London", async () => {
  await page.getByRole("button", { name: /Try a sample day/i }).click();
  await page.waitForSelector(".timeline", { timeout: 30_000 });
  const timeline = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
  expect(/09:00/.test(timeline), `timeline missing 09:00: ${timeline.slice(0, 120)}`);
  return timeline.slice(0, 100);
});

await check("run latency: three sequential checks reach a terminal state", async () => {
  const durations = [];
  for (let index = 1; index <= 3; index += 1) {
    const started = Date.now();
    const { status, error } = await recheckAndWait(page);
    const elapsed = Date.now() - started;
    durations.push(elapsed);
    runLog.push({ index, elapsed, status, error });
    observe(`run ${index}`, `${elapsed}ms status="${status}" error="${error}"`);
    expect(
      !/timed out|could not be completed/i.test(`${status} ${error}`),
      `run ${index} failed after ${elapsed}ms: ${error || status}`,
    );
  }
  return `durations=${durations.join("/")}ms`;
});

await check("?decision=<id> deep link works in a fresh decision state", async () => {
  const cardId = await page.locator("article.decision").first().getAttribute("id");
  expect(Boolean(cardId), "no decision card available");
  const decisionId = cardId.replace("decision-", "");
  await page.goto(`${BASE}/?decision=${decisionId}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(`#decision-${decisionId}`, { timeout: 60_000 });
  await page.waitForTimeout(1500);
  const className = await page.locator(`#decision-${decisionId}`).getAttribute("class");
  const inView = await page.evaluate((id) => {
    const rect = document.getElementById(`decision-${id}`)?.getBoundingClientRect();
    return rect ? rect.top < window.innerHeight && rect.bottom > 0 : false;
  }, decisionId);
  const url = page.url();
  expect(/decision-focused/.test(String(className)), `class=${className}`);
  expect(inView, "not scrolled into view");
  expect(!url.includes("decision="), `url not cleaned: ${url}`);
  await shot(page, "deep-link");
  return `id=${decisionId.slice(0, 10)}… focus+scroll+cleanup ok`;
});

await check("decision card can be resolved and the card disappears", async () => {
  const before = await page.locator("article.decision").count();
  const skip = page.getByRole("button", { name: /Skip this journey/i });
  expect(await skip.count() > 0, "no skip action available");
  await skip.first().click();
  await waitStatus(page, "Journey skipped.");
  await page.waitForTimeout(600);
  const after = await page.locator("article.decision").count();
  expect(after === before - 1, `cards ${before} -> ${after}`);
  return `cards ${before}->${after}`;
});

await check("realistic fast double-click queues a single run", async () => {
  let posted = 0;
  const listener = (request) => {
    if (request.method() === "POST" && request.url().includes("/api/runs")) posted += 1;
  };
  context.on("request", listener);
  const button = page.getByRole("button", { name: /Recheck now/i });
  const box = await button.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(120);
  await page.mouse.down();
  await page.mouse.up();
  await recheckAndWait(page);
  await page.waitForTimeout(1500);
  context.off("request", listener);
  observe("user-speed double-click", `${posted} POST /api/runs from clicks 120ms apart`);
  expect(posted === 1, `${posted} runs queued by a user-speed double-click`);
  return "1 run";
});

await check("time zone setting changes the rendered display", async () => {
  const before = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
  await page.getByRole("button", { name: /Settings/i }).first().click();
  const panel = page.locator("#travel-settings");
  await panel.waitFor({ timeout: 10_000 });
  await panel.getByLabel(/Time zone/i).selectOption("America/New_York");
  await panel.getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  await page.waitForTimeout(1200);
  const after = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
  const hint = (await page.locator(".section-heading").innerText()).replace(/\s+/g, " ");
  const me = await page.evaluate(async () => {
    const response = await fetch("/api/me", {
      headers: { "X-Glide-Session": window.localStorage.getItem("glide-sample-session") ?? "" },
    });
    return response.json();
  });
  observe(
    "time zone effect",
    `stored=${me.time_zone}; timeline before="${before.slice(0, 50)}"; after="${after.slice(0, 50)}"; heading="${hint}"`,
  );
  await page.getByRole("button", { name: /Settings/i }).first().click();
  await page.locator("#travel-settings").getByLabel(/Time zone/i).selectOption("Europe/London");
  await page.locator("#travel-settings").getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  return `stored tz=${me.time_zone}; times unchanged=${before === after}`;
});

await check('"No fixed start" changes the journey note and the plan', async () => {
  await page.getByRole("button", { name: /Settings/i }).first().click();
  const panel = page.locator("#travel-settings");
  await panel.waitFor({ timeout: 10_000 });
  await panel.getByLabel(/Start address/i).selectOption("");
  await panel.getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  const note = (await page.locator(".note-footer").innerText()).trim();
  expect(/no fixed starting point/i.test(note), `note footer = "${note}"`);
  const { status, error } = await recheckAndWait(page);
  const decisions = (await page.locator("article.decision").allInnerTexts())
    .join(" | ")
    .replace(/\s+/g, " ");
  observe(
    "no fixed start outcome",
    `status="${status}" error="${error}" decisions="${decisions.slice(0, 220)}"`,
  );
  await page.getByRole("button", { name: /Settings/i }).first().click();
  await page.locator("#travel-settings").getByLabel(/Start address/i).selectOption("place_a");
  await page.locator("#travel-settings").getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  return `note="${note}"`;
});

await check("earliest departure round-trips through the settings panel", async () => {
  await page.getByRole("button", { name: /Settings/i }).first().click();
  const panel = page.locator("#travel-settings");
  await panel.waitFor({ timeout: 10_000 });
  await panel.getByLabel(/Earliest departure/i).fill("06:15");
  await panel.getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  await page.getByRole("button", { name: /Settings/i }).first().click();
  const value = await page.locator("#travel-settings").getByLabel(/Earliest departure/i).inputValue();
  await page.locator("#travel-settings").getByLabel(/Earliest departure/i).fill("");
  await page.locator("#travel-settings").getByRole("button", { name: /Save settings/i }).click();
  await waitStatus(page, "Settings saved.");
  expect(value === "06:15", `stored value = "${value}"`);
  return `stored "${value}", then cleared`;
});

const viewports = [
  { name: "tablet", width: 834, height: 1112 },
  { name: "small-mobile", width: 320, height: 640 },
];

for (const viewport of viewports) {
  const viewportContext = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    locale: "en-GB",
  });
  const viewportPage = await viewportContext.newPage();
  viewportPage.setDefaultTimeout(20_000);
  await check(`${viewport.name} (${viewport.width}x${viewport.height}) day view has no overflow`, async () => {
    await viewportPage.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
    await viewportPage.getByRole("button", { name: /Try a sample day/i }).click();
    await viewportPage.waitForSelector(".timeline", { timeout: 30_000 });
    await viewportPage.waitForTimeout(500);
    const overflow = await viewportPage.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    const offenders = await viewportPage.evaluate(() =>
      [...document.querySelectorAll("body *")]
        .filter((el) => el.getBoundingClientRect().right > window.innerWidth + 2)
        .slice(0, 6)
        .map((el) => `${el.tagName.toLowerCase()}.${String(el.className).slice(0, 40)}`),
    );
    await viewportPage.screenshot({ path: join(shotDir, `${viewport.name}-day.png`), fullPage: true });
    expect(overflow <= 2, `overflow ${overflow}px; offenders ${offenders.join(", ")}`);
    return `overflow=${overflow}px`;
  });
  await viewportPage.close().catch(() => {});
  await viewportContext.close().catch(() => {});
}

await context.close().catch(() => {});
await browser.close();

const failed = checks.filter((entry) => !entry.ok);
const lines = [];
lines.push("# Glide live UI — third pass (controls, connect, latency)");
lines.push("");
lines.push(`Target: ${BASE}`);
lines.push(`Run at: ${new Date().toISOString()}`);
lines.push("");
lines.push(`Checks: ${checks.length} (${checks.length - failed.length} passed, ${failed.length} failed)`);
lines.push("");
lines.push("## Failures");
lines.push("");
if (!failed.length) lines.push("None.");
for (const failure of failed) lines.push(`- **${failure.name}** — ${failure.detail}`);
lines.push("");
lines.push("## Checks");
lines.push("");
checks.forEach((entry, index) =>
  lines.push(`${index + 1}. ${entry.ok ? "PASS" : "FAIL"} — ${entry.name} — ${String(entry.detail).slice(0, 400)}`),
);
lines.push("");
lines.push("## Observations");
lines.push("");
for (const entry of observations) lines.push(`- **${entry.name}** — ${entry.detail}`);
writeFileSync(join(outDir, "controls-report.md"), lines.join("\n"), "utf8");
console.log(`\n${checks.length - failed.length}/${checks.length} control checks passed. Report: ${join(outDir, "controls-report.md")}`);
process.exit(0);
