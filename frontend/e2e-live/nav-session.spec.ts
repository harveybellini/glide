// Live audit area: navigation, session lifecycle and deep links.
//
// Covers the paths a judge takes around the product shell rather than inside
// one screen: first-visit landing, unknown URLs, starting and returning to a
// sample session, an expired/unknown stored session, the ?decision=<id> email
// deep link, and Reset sample.
//
// Read-only against the live site: sample sessions are disposable server-side
// state created through the public API.
import { test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import {
  Audit,
  LIVE_BASE,
  SESSION_KEY,
  apiGet,
  createSession,
  expect,
  inventory,
  instrument,
  openWithSession,
  squeeze,
} from "./harness";

const audit = new Audit("nav-session");

// A failing test makes Playwright restart the worker process, and each worker
// gets its own Audit instance, so the shared report is written per test (keyed
// by title) and merged offline. afterAll still emits the harness report for the
// tests that ran in the final worker.
function flush(title: string) {
  const dir = path.resolve(process.cwd(), "..", "temp", "live-audit", "reports");
  fs.mkdirSync(dir, { recursive: true });
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  fs.writeFileSync(
    path.join(dir, `nav-session-${slug}.json`),
    JSON.stringify(
      { area: "nav-session", test: title, findings: audit.findings, steps: audit.steps },
      null,
      2,
    ),
  );
}

test.afterEach(({}, testInfo) => {
  flush(testInfo.title);
});

test.afterAll(() => {
  audit.write();
});

interface DayResponse {
  date: string;
  decisions: { id: string; reason: string }[];
  travel_blocks: unknown[];
  source_events: unknown[];
  last_run?: { status: string } | null;
}

interface RunResponse {
  run: { id: string; status: string };
}

const TERMINAL = new Set(["completed", "needs_input", "failed", "superseded", "paused"]);

async function pollRun(
  request: import("@playwright/test").APIRequestContext,
  runId: string,
  sessionId: string,
  timeoutMs = 60_000,
): Promise<RunResponse["run"]> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const response = await request.get(`${LIVE_BASE}/api/runs/${runId}`, {
      headers: { "X-Glide-Session": sessionId },
    });
    if (response.ok()) {
      const payload = (await response.json()) as RunResponse;
      if (TERMINAL.has(payload.run.status)) return payload.run;
    }
    if (Date.now() >= deadline) {
      throw new Error(`run ${runId} did not reach a terminal status in ${timeoutMs} ms`);
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

async function statsText(page: import("@playwright/test").Page): Promise<string> {
  return squeeze(await page.locator(".day-stats").innerText());
}

// Records the paint sequence after a navigation that already carries a stored
// session: which surface paints first (the marketing landing page or the day
// view), when it painted, and when the day view arrived. A returning visitor
// should not be shown marketing copy while their day loads.
async function paintSequence(
  page: import("@playwright/test").Page,
): Promise<{ first: string; firstMs: number; dayMs: number }> {
  return page.evaluate(
    () =>
      new Promise<{ first: string; firstMs: number; dayMs: number }>((resolve) => {
        const started = Date.now();
        let first: string | null = null;
        let firstMs = 0;
        const timer = window.setInterval(() => {
          const elapsed = Date.now() - started;
          if (!first && document.querySelector("main.landing")) {
            first = "landing";
            firstMs = elapsed;
          }
          if (document.querySelector("#timeline")) {
            window.clearInterval(timer);
            resolve({ first: first ?? "day", firstMs: first ? firstMs : elapsed, dayMs: elapsed });
          } else if (elapsed > 20_000) {
            window.clearInterval(timer);
            resolve({ first: first ?? "timeout", firstMs, dayMs: -1 });
          }
        }, 10);
      }),
  );
}

test("first visit shows the landing page, and unknown URLs still boot the app", async ({
  page,
}) => {
  const watch = instrument(page, audit);

  const landing = await page.goto(LIVE_BASE, { waitUntil: "networkidle" });
  audit.step("GET /", String(landing?.status() ?? "no response"));
  expect(landing?.status()).toBe(200);
  await expect(page.getByRole("button", { name: /Try a sample day/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Life happens/i })).toBeVisible();
  const stored = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  audit.step("session key on first visit", stored === null ? "absent" : String(stored));
  expect(stored, "a first-time visitor must not have a sample session").toBeNull();
  await audit.shot(page, "first-visit-landing");

  // CloudFront maps 403/404 to index.html (200), so a deep link that is not a
  // real file must still boot the app rather than showing an error page.
  const deep = await page.goto(`${LIVE_BASE}/some/deep/unknown/route`, {
    waitUntil: "networkidle",
  });
  audit.step("GET /some/deep/unknown/route", String(deep?.status() ?? "no response"));
  expect(deep?.status(), "SPA fallback for unknown paths").toBe(200);
  await expect(page.getByRole("button", { name: /Try a sample day/i })).toBeVisible();
  audit.step("unknown path rendered", "landing");
  audit.step("unknown path URL", page.url());

  const controls = await inventory(page);
  audit.dump("landing-inventory", controls);
  watch.assertClean("landing");
  audit.ok("Landing and SPA fallback", "GET / and GET /some/deep/unknown/route both 200 and rendered");
});

test("Try a sample day opens the day view and stores one session", async ({ page }) => {
  const watch = instrument(page, audit);

  await page.goto(LIVE_BASE, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /Try a sample day/i }).click();

  await page.getByRole("heading", { name: /Your day/i }).waitFor({ timeout: 30_000 });
  const stats = await statsText(page);
  const sessionId = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  audit.step("sample session id", String(sessionId));
  audit.step("day stats", stats);
  expect(sessionId, "starting a sample stores a session id").toMatch(/^[0-9a-f]{8,}$/i);
  expect(squeeze(await page.locator(".mode-badge").innerText())).toContain("Sample workspace");

  const events = Number(stats.match(/^(\d+)/)?.[1] ?? "0");
  audit.step("appointments in timeline", String(events));
  expect(events, "the sample day ships with appointments").toBeGreaterThan(0);
  await audit.shot(page, "sample-day-opened");

  watch.assertClean("sample day");
  audit.ok("Sample start", `day view rendered with ${stats}`);
});

test("a stored sample session survives reload without a landing-page flash", async ({ page }) => {
  const sessionId = await createSession();
  const before = await apiGet<DayResponse>("/api/day", sessionId);
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, sessionId],
  );

  const response = await page.goto(LIVE_BASE, { waitUntil: "commit" });
  audit.step("GET / with a stored session", String(response?.status() ?? "no response"));

  const paints = await paintSequence(page);
  audit.step(
    "first surface after reload",
    paints.first,
    `${paints.firstMs} ms after the document was committed; day view at ${paints.dayMs} ms`,
  );
  await page.locator("#timeline").waitFor({ timeout: 30_000 });
  const stats = await statsText(page);
  audit.step("day stats after reload", stats);
  expect(stats).toContain(String(before.source_events.length).padStart(2, "0"));

  const after = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  expect(after, "reload must reuse the stored session").toBe(sessionId);
  audit.step("session id after reload", `unchanged (${after})`);
  await audit.shot(page, "reload-with-session");

  if (paints.first === "landing") {
    audit.low(
      "Returning sample visitor is shown the marketing landing page while the day loads",
      `With a valid sample session in localStorage, the first painted surface was main.landing at ` +
        `${paints.firstMs} ms and the day view did not replace it until ${paints.dayMs} ms, so the ` +
        `marketing hero is on screen for roughly ${paints.dayMs - paints.firstMs} ms. The live (Google) path avoids ` +
        `this with the glide-live-hint boot screen, but the hint is only set when a live day loads ` +
        `(frontend/src/App.tsx:111), so sample visitors see marketing copy flash. Repro: start a sample, ` +
        `reload, watch the first paint.`,
      `first surface=${paints.first} at ${paints.firstMs} ms, day view at ${paints.dayMs} ms; url=${page.url()}`,
    );
  } else {
    audit.ok(
      "No landing flash on sample reload",
      `first surface was ${paints.first} at ${paints.firstMs} ms, day view at ${paints.dayMs} ms`,
    );
  }
});

test("an unknown stored session recovers to the landing page", async ({ page }) => {
  const watch = instrument(page, audit);
  const bogus = "0".repeat(31) + "d";
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, bogus],
  );

  await page.goto(LIVE_BASE, { waitUntil: "networkidle" });
  await expect(page.getByRole("button", { name: /Try a sample day/i })).toBeVisible({
    timeout: 20_000,
  });
  const cleared = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  audit.step("session key after recovery", cleared === null ? "cleared" : String(cleared));
  audit.step("alert/error text", String(await page.locator("[role=alert]").allInnerTexts()));
  expect(cleared, "an unusable session id must be cleared").toBeNull();
  await expect(page.locator("[role=alert]")).toHaveCount(0);
  await audit.shot(page, "stale-session-recovered");

  // The 404s from /api/day, /api/activity and /api/me are the expected probe
  // for the bogus id, and Chromium logs each one to the console, so only real
  // script exceptions and unexpected statuses are treated as findings here.
  const unexpected = watch.unexpectedHttpErrors([404]);
  audit.step("HTTP statuses during recovery", watch.apiCalls.map((c) => `${c.status} ${c.url}`).join(", "));
  expect(unexpected, "no unexpected HTTP errors during recovery").toEqual([]);
  expect(watch.pageErrors, "no uncaught script errors during recovery").toEqual([]);
  for (const error of watch.consoleErrors) {
    if (!/404|Failed to load resource/i.test(error)) {
      audit.medium("Console error during stale-session recovery", error);
    }
  }
  audit.ok(
    "Unknown session recovery",
    "landing page rendered, localStorage cleared, no alert shown",
  );
});

test("?decision=<id> focuses the matching card and clears itself", async ({ page }) => {
  const watch = instrument(page, audit);
  const sessionId = await createSession();

  const queued = await page.request.post(`${LIVE_BASE}/api/runs`, {
    headers: { "X-Glide-Session": sessionId, "Content-Type": "application/json" },
    data: { trigger: "sample" },
  });
  audit.step("POST /api/runs", String(queued.status()));
  expect(queued.status(), "sample check accepted").toBeLessThan(300);
  const { run_id } = (await queued.json()) as { run_id: string };
  const run = await pollRun(page.request, run_id, sessionId);
  audit.step("run terminal status", run.status);

  const day = await apiGet<DayResponse>("/api/day", sessionId);
  audit.step("decisions after first check", String(day.decisions.length));
  if (!day.decisions.length) {
    audit.high(
      "Fresh sample check produced no decision to deep-link to",
      "The documented sample flow is: first check -> one travel block plus one shortfall decision. " +
        `This run ended ${run.status} with no open decision, so the ?decision=<id> email deep link could ` +
        "not be exercised with a real id.",
      `run=${run_id} status=${run.status} decisions=0`,
    );
    return;
  }
  const decisionId = day.decisions[0].id;

  await openWithSession(page, sessionId, `/?decision=${decisionId}`);
  await page.locator("#timeline").waitFor({ timeout: 30_000 });
  await page
    .locator(".decisions .decision")
    .first()
    .waitFor({ state: "visible", timeout: 20_000 });
  const rendered = await page.locator(".decisions .decision").count();
  audit.step("decision cards rendered", String(rendered));

  const card = page.locator(`[id="decision-${decisionId}"]`);
  const cardCount = await card.count();
  const focused = await page.locator(".decision-focused").count();
  audit.step("cards carrying id=decision-<id>", String(cardCount));
  audit.step("cards carrying .decision-focused", String(focused));
  audit.step("url after load", page.url());
  await audit.shot(page, "decision-deep-link");

  if (cardCount === 0) {
    // The card renders, so the day and its decision are live; only the
    // deep-link affordance from the current source tree is missing.
    audit.high(
      "?decision=<id> email deep link is not in the deployed bundle",
      "The source tree renders the decision card with id={`decision-${decision.id}`} and applies " +
        "class `decision-focused`, then strips the query parameter and scrolls the card into view " +
        "(frontend/src/App.tsx:60-104). The deployed bundle renders the same card as " +
        "`<article class=\"decision\">` with no id, no highlight and no scroll: the query parameter is " +
        "never read. A judge opening the link from a decision email lands at the top of the day with no " +
        "indication of which decision needs input.",
      `card[id="decision-${decisionId}"] count=0, .decision-focused count=${focused}, url=${page.url()} ` +
        "(deployed assets verified separately in the bundle probe test)",
    );
    // Only in-view scrolling is asserted once the feature ships.
    expect(page.url(), "deployed bundle cannot consume the parameter").toContain("decision=");
    watch.assertClean("decision deep link (feature absent)");
    return;
  }

  await expect(card).toHaveClass(/decision-focused/);
  const inView = await card.evaluate((node) => {
    const box = node.getBoundingClientRect();
    return box.top >= 0 && box.bottom <= window.innerHeight + 1;
  });
  audit.step("focused card scrolled into view", String(inView));
  expect(inView, "the deep-linked card is scrolled into view").toBe(true);
  expect(page.url(), "the decision query param is consumed").not.toContain("decision=");

  await page.waitForTimeout(7_000);
  await expect(card).not.toHaveClass(/decision-focused/, { timeout: 5_000 });
  audit.step("focus highlight cleared after 6 s", "yes");

  watch.assertClean("decision deep link");
  audit.ok(
    "Decision email deep link",
    `${decisionId} highlighted, scrolled into view, param stripped, highlight cleared`,
  );
});

test("deployed frontend bundle carries the source features this area exercises", async ({
  page,
}) => {
  const index = await page.request.get(LIVE_BASE);
  const html = await index.text();
  const scripts = [...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map((match) => match[1]);
  audit.step("deployed script tags", scripts.join(", "));
  const bundle: string[] = [];
  for (const src of scripts) {
    const url = src.startsWith("http") ? src : `${LIVE_BASE}${src}`;
    const response = await page.request.get(url);
    audit.step(`GET ${src}`, String(response.status()));
    if (response.ok()) bundle.push(await response.text());
  }
  const source = bundle.join("\n");
  audit.step("bundle bytes", String(source.length));

  // Markers that exist in frontend/src today, with the user-visible feature
  // they gate. `Sample reset to its starting state` is the control: it shipped
  // and is exercised by the reset test above.
  const markers: { marker: string; feature: string }[] = [
    { marker: "Sample reset to its starting state", feature: "reset sample" },
    { marker: "decision-focused", feature: "?decision=<id> deep link" },
    { marker: "glide-live-hint", feature: "boot screen for a returning live visitor" },
    { marker: "Checking your calendar", feature: "boot screen copy" },
    { marker: "notify_on_decisions", feature: "decision notification settings" },
  ];
  const report = markers.map(({ marker, feature }) => ({
    feature,
    marker,
    present: source.includes(marker),
  }));
  audit.dump("bundle-markers", report);
  for (const entry of report) {
    audit.step(`bundle marker ${entry.marker}`, entry.present ? "present" : "absent");
  }
  const missing = report.filter((entry) => !entry.present && entry.marker !== "Sample reset to its starting state");
  if (missing.length) {
    audit.medium(
      "Deployed bundle predates features that exist in the working tree",
      `The published ${scripts.length} asset(s) do not contain: ` +
        missing.map((entry) => `\`${entry.marker}\` (${entry.feature})`).join(", ") +
        ". The same features are present in frontend/src, so the live site is one frontend publish behind.",
      JSON.stringify(missing),
    );
  } else {
    audit.ok("Deployed bundle matches the working tree markers");
  }
  expect(report[0].present, "control marker proves the bundle was fetched").toBe(true);
});

test("API: an unknown or missing sample session is reported as an error, not as HTML", async ({
  page,
}) => {
  const bogus = "f".repeat(31) + "a";
  const unknown = await page.request.get(`${LIVE_BASE}/api/day`, {
    headers: { "X-Glide-Session": bogus },
  });
  const unknownBody = await unknown.text();
  audit.step(
    "GET /api/day with an unknown session",
    String(unknown.status()),
    `content-type=${unknown.headers()["content-type"]} x-cache=${unknown.headers()["x-cache"] ?? "-"} ` +
      `body=${JSON.stringify(unknownBody.slice(0, 60))}`,
  );

  const anonymous = await page.request.get(`${LIVE_BASE}/api/day`);
  audit.step(
    "GET /api/day with no session",
    String(anonymous.status()),
    `content-type=${anonymous.headers()["content-type"]} ` +
      `body=${JSON.stringify((await anonymous.text()).slice(0, 60))}`,
  );

  const sessionId = await createSession();
  const missingRun = await page.request.get(`${LIVE_BASE}/api/runs/does-not-exist`, {
    headers: { "X-Glide-Session": sessionId },
  });
  audit.step(
    "GET /api/runs/does-not-exist",
    String(missingRun.status()),
    `content-type=${missingRun.headers()["content-type"]} ` +
      `body=${JSON.stringify((await missingRun.text()).slice(0, 60))}`,
  );

  const htmlWhereErrorExpected = [
    ["unknown session", unknown.status(), unknown.headers()["content-type"] ?? ""],
    ["missing run", missingRun.status(), missingRun.headers()["content-type"] ?? ""],
  ].filter(([, status, type]) => String(type).includes("text/html") && Number(status) < 400);
  if (htmlWhereErrorExpected.length) {
    audit.high(
      "API 404s are rewritten into the SPA fallback (HTTP 200 + index.html)",
      "CloudFront's distribution-level CustomErrorResponses map 404 (and 403) to 200 with " +
        "`/index.html` (infra/template.yaml:144-150). That mapping is not scoped to the S3 " +
        "behavior, so it also covers the `/api/*` behavior: a documented 404 from the API reaches " +
        "clients as HTTP 200 with an HTML body. The single-page app survives because its JSON " +
        "parse throws and it clears the session, but any script or judge using curl/the API " +
        "contract cannot tell a missing sample session from a successful call. Seen for: " +
        htmlWhereErrorExpected.map(([name, status, type]) => `${name} -> ${status} ${type}`).join("; "),
      JSON.stringify(htmlWhereErrorExpected),
    );
  } else {
    audit.ok("API errors keep their status codes", "no 200 + text/html seen for missing resources");
  }
  expect(anonymous.status(), "no session at all must not be served a day").toBeGreaterThanOrEqual(400);
});

test("Reset sample returns the day to its starting state", async ({ page }) => {
  const watch = instrument(page, audit);
  const sessionId = await createSession();

  const queued = await page.request.post(`${LIVE_BASE}/api/runs`, {
    headers: { "X-Glide-Session": sessionId, "Content-Type": "application/json" },
    data: { trigger: "sample" },
  });
  const { run_id } = (await queued.json()) as { run_id: string };
  await pollRun(page.request, run_id, sessionId);
  const planned = await apiGet<DayResponse>("/api/day", sessionId);
  audit.step(
    "day before reset",
    `blocks=${planned.travel_blocks.length} decisions=${planned.decisions.length}`,
  );

  await openWithSession(page, sessionId);
  await page.getByRole("button", { name: /Reset sample/i }).click();
  await expect(page.locator(".status")).toHaveText(/Sample reset to its starting state/i, {
    timeout: 30_000,
  });
  const stats = await statsText(page);
  audit.step("day stats after reset", stats);
  const reset = await apiGet<DayResponse>("/api/day", sessionId);
  audit.step(
    "day after reset",
    `blocks=${reset.travel_blocks.length} decisions=${reset.decisions.length}`,
  );
  expect(stats, "reset removes travel blocks").toContain("00");
  expect(reset.travel_blocks.length, "no travel blocks left after reset").toBe(0);
  expect(reset.decisions.length, "no decisions left after reset").toBe(0);
  const newId = await page.evaluate((key) => window.localStorage.getItem(key), SESSION_KEY);
  audit.step("session id after reset", String(newId));
  await audit.shot(page, "after-reset");

  watch.assertClean("reset sample");
  audit.ok("Reset sample", `day back to its starting state (${stats})`);
});

test("an unknown ?decision= id does not stick around forever", async ({ page }) => {
  const sessionId = await createSession();
  await openWithSession(page, sessionId, "/?decision=00000000-0000-0000-0000-000000000000");
  await page.locator("#timeline").waitFor({ timeout: 30_000 });
  await page.waitForTimeout(2_000);
  const url = page.url();
  audit.step("url with unknown decision id", url);
  const focused = await page.locator(".decision-focused").count();
  audit.step("focused cards with unknown id", String(focused));
  expect(focused, "no card can be focused by an unknown id").toBe(0);
  if (url.includes("decision=")) {
    audit.nit(
      "Unknown ?decision=<id> stays in the address bar",
      "In the deployed bundle the parameter is never consumed at all (see the " +
        "missing-deep-link finding). Once that ships, note that the focus effect only strips the " +
        "parameter when the id matches an open decision (frontend/src/App.tsx:99), so an expired " +
        "or mistyped link would keep ?decision=... in the URL for the rest of the session. " +
        "Cosmetic only: nothing is highlighted, but the stale parameter is copied into bookmarks " +
        "and reloads.",
      url,
    );
  } else {
    audit.ok("Unknown decision id cleared from the URL");
  }
});
