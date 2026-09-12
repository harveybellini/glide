// Independent verification of the headline live-audit claims.
//
// Deliberately does not repeat the broad crawl: it re-tests three specific
// claims (buffer bounds, skip-journey resolution, unknown API route) with raw
// request/response evidence, and records a deployed-bundle stamp so the team
// can tell which build a finding applies to.
//
// Report: temp/live-audit/reports/verify-core-claims.md
import * as fs from "node:fs";
import * as path from "node:path";
import { expect, test } from "@playwright/test";
import { Audit } from "./harness";

const BASE =
  process.env.PLAYWRIGHT_BASE_URL ?? "https://d3tvxy281s2u11.cloudfront.net";

const audit = new Audit("verify-core-claims");
test.afterAll(() => audit.write());

interface CallResult {
  status: number;
  contentType: string | null;
  body: any;
  raw: string;
}

async function call(
  pathname: string,
  init: RequestInit = {},
  sessionId?: string,
): Promise<CallResult> {
  const response = await fetch(`${BASE}${pathname}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(sessionId ? { "X-Glide-Session": sessionId } : {}),
      ...((init.headers as Record<string, string> | undefined) ?? {}),
    },
  });
  const raw = await response.text();
  let body: any = null;
  try {
    body = JSON.parse(raw);
  } catch {
    body = null;
  }
  return {
    status: response.status,
    contentType: response.headers.get("content-type"),
    body,
    raw,
  };
}

const TERMINAL = new Set([
  "completed",
  "needs_input",
  "failed",
  "superseded",
  "paused",
]);

async function waitRun(sessionId: string, runId: string, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let last: CallResult = await call(`/api/runs/${runId}`, {}, sessionId);
  while (!TERMINAL.has(last.body?.run?.status) && Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    last = await call(`/api/runs/${runId}`, {}, sessionId);
  }
  return last;
}

async function runSampleCheck(sessionId: string) {
  const queued = await call(
    "/api/runs",
    { method: "POST", body: JSON.stringify({ trigger: "sample" }) },
    sessionId,
  );
  const queuedRunId = queued.body?.run_id ?? queued.body?.run?.id;
  expect(queued.status, "queue run status").toBeLessThan(300);
  expect(queuedRunId, "queued run id").toBeTruthy();
  return waitRun(sessionId, queuedRunId as string);
}

// POST /api/demo/session with one retry per attempt, per the audit brief
// (record transient 5xx honestly instead of treating a single blip as a defect).
async function createSessionWithRetry(attempts = 3) {
  const seen: string[] = [];
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await call("/api/demo/session", {
      method: "POST",
      body: "{}",
    });
    const sessionId = response.body?.session?.session_id ?? null;
    seen.push(`attempt${attempt}=${response.status}${sessionId ? "" : " (no session)"}`);
    if (response.status < 300 && sessionId) {
      return { response, sessionId, seen };
    }
    if (attempt < attempts) {
      await new Promise((resolve) => setTimeout(resolve, 1500 * attempt));
    }
  }
  return { response: null, sessionId: null, seen };
}

// Offsets the wall-clock start of an existing sample event while keeping the
// ISO shape the live API accepts (`2026-09-11T09:00:00+01:00`).
function shiftWallClock(value: string, minutes: number): string {
  const datePart = value.slice(0, 10);
  const offset = value.slice(19); // e.g. "+01:00"
  const [hours, mins] = value.slice(11, 16).split(":").map(Number);
  const total = hours * 60 + mins + minutes;
  const next = ((total % 1440) + 1440) % 1440;
  const hh = String(Math.floor(next / 60)).padStart(2, "0");
  const mm = String(next % 60).padStart(2, "0");
  return `${datePart}T${hh}:${mm}:00${offset}`;
}

test("arrival buffer: UI rejects out-of-range, API is checked too", async ({
  page,
}) => {
  test.setTimeout(150_000);
  // Bound every action so a stalled control is reported instead of eating the
  // whole test budget (Playwright's default action timeout is unlimited).
  page.setDefaultTimeout(15_000);
  const createResponses: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/api/demo/session")) {
      createResponses.push(`${response.request().method()} ${response.status()}`);
    }
  });
  const startedAt = Date.now();
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page
    .getByRole("button", { name: /try a sample day/i })
    .click({ timeout: 20_000 });
  let reachedWorkspace = true;
  try {
    await page
      .getByText(/sample workspace/i)
      .first()
      .waitFor({ state: "visible", timeout: 25_000 });
  } catch {
    reachedWorkspace = false;
  }
  if (!reachedWorkspace) {
    const landingError = await page
      .locator(".landing .error")
      .innerText()
      .catch(() => "");
    audit.step(
      "sample day",
      "not reached",
      `elapsedMs=${Date.now() - startedAt} api=${JSON.stringify(createResponses)} error="${landingError}"`,
    );
    audit.high(
      "Try a sample day did not open the workspace",
      "The primary call to action did not reach the day view; the sample day is the hosted demo judges see first.",
      `POST /api/demo/session -> ${JSON.stringify(createResponses)}; landing error="${landingError}"; elapsedMs=${Date.now() - startedAt}`,
    );
    return;
  }
  audit.step(
    "sample day",
    "reached",
    `elapsedMs=${Date.now() - startedAt} api=${JSON.stringify(createResponses)}`,
  );
  const sessionId = await page.evaluate(() =>
    localStorage.getItem("glide-sample-session"),
  );
  expect(sessionId, "sample session id in localStorage").toBeTruthy();
  audit.step("sample session", "created", sessionId);

  const before = await call("/api/me", {}, sessionId ?? undefined);
  const originalPadding = before.body?.padding_minutes;
  audit.step("padding before", "read", String(originalPadding));

  // (a) The UI path a normal user takes.
  const openSettings = page.getByRole("button", { name: /^settings$/i });
  await audit.shot(page, "day-before-settings");
  audit.step(
    "settings button",
    "found",
    `count=${await openSettings.count()} disabled=${await openSettings
      .first()
      .isDisabled()
      .catch(() => "unknown")}`,
  );
  await openSettings.first().click({ timeout: 15_000 });
  const panel = page.locator("#travel-settings");
  await panel.waitFor({ state: "visible", timeout: 15_000 });
  audit.step("settings panel", "open", "");
  await panel.getByLabel(/arrival buffer/i).fill("99", { timeout: 15_000 });
  await panel
    .getByRole("button", { name: /save settings/i })
    .click({ timeout: 15_000 });
  const inputState = await panel
    .getByLabel(/arrival buffer/i)
    .evaluate((element) => {
      const input = element as HTMLInputElement;
      return {
        valid: input.validity.valid,
        message: input.validationMessage,
        value: input.value,
      };
    })
    .catch(() => null);
  const uiError =
    (await panel
      .locator(".error")
      .innerText()
      .catch(() => "")) || (inputState?.message ?? "");
  const afterUi = await call("/api/me", {}, sessionId ?? undefined);
  await audit.shot(page, "buffer-99-attempt");
  if (
    inputState &&
    !inputState.valid &&
    afterUi.body?.padding_minutes === originalPadding
  ) {
    audit.ok(
      "UI rejects a 99-minute arrival buffer",
      `Native constraint validation blocked the save ("${inputState.message}") and /api/me still reports padding_minutes=${originalPadding}.`,
    );
  } else if (!uiError && afterUi.body?.padding_minutes === 99) {
    audit.high(
      "Out-of-range arrival buffer accepted through the UI",
      "Saving 99 minutes succeeded and /api/me reports padding_minutes=99.",
      `uiError="${uiError}" apiPadding=${afterUi.body?.padding_minutes}`,
    );
  } else {
    audit.medium(
      "Arrival buffer 99 produced an unexpected result",
      "Neither a clean rejection nor a clean save was observed.",
      `uiError="${uiError}" apiPadding=${afterUi.body?.padding_minutes}`,
    );
  }

  // (b) The server contract: a direct API call must not accept 99 either.
  const apiPatch = await call(
    "/api/settings",
    { method: "PATCH", body: JSON.stringify({ padding_minutes: 99 }) },
    sessionId ?? undefined,
  );
  const apiAfter = await call("/api/me", {}, sessionId ?? undefined);
  if (apiPatch.status < 300 && apiAfter.body?.padding_minutes === 99) {
    audit.medium(
      "Server accepts out-of-range arrival buffer (client-only validation)",
      "PATCH /api/settings with padding_minutes=99 returned success and the value persisted. The form blocks it, but the API contract does not.",
      `PATCH status=${apiPatch.status} body=${JSON.stringify(apiPatch.body)} padding_minutes=${apiAfter.body?.padding_minutes}`,
    );
  } else if (apiPatch.status >= 400) {
    audit.ok(
      "API rejects out-of-range arrival buffer",
      `PATCH /api/settings padding_minutes=99 -> ${apiPatch.status} ${JSON.stringify(apiPatch.body)}`,
    );
  }

  // Restore a sane value so the session stays representative.
  await call(
    "/api/settings",
    {
      method: "PATCH",
      body: JSON.stringify({ padding_minutes: originalPadding ?? 15 }),
    },
    sessionId ?? undefined,
  );
  await panel
    .getByRole("button", { name: /cancel/i })
    .click()
    .catch(() => undefined);
});

test("skip_journey: the resolved decision must leave the open list", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const created = await createSessionWithRetry();
  audit.step(
    "create sample session",
    created.sessionId ? "ok" : "failed",
    created.seen.join(", "),
  );
  if (!created.sessionId) {
    audit.high(
      "POST /api/demo/session fails for a fresh visitor",
      "Creating the hosted sample session did not succeed after retries with backoff, so nothing behind the landing page can be reached.",
      created.seen.join(", "),
    );
    return;
  }
  const sessionId = created.sessionId;

  await runSampleCheck(sessionId);
  let day = await call("/api/day", {}, sessionId);
  let decisions: any[] = day.body?.decisions ?? [];

  // Reproduce the reported full-pass sequence exactly: tighten the last leg so
  // a journey cannot fit, recheck, then skip that specific decision.
  const events = [...(day.body?.source_events ?? [])].sort((a, b) =>
    String(a.start).localeCompare(String(b.start)),
  );
  expect(events.length, "sample events to edit").toBeGreaterThan(1);
  const previous = events[events.length - 2];
  const last = events[events.length - 1];
  const movedStart = shiftWallClock(String(previous.end), 5);
  const durationMinutes = Math.max(
    5,
    Math.round(
      (Date.parse(String(last.end)) - Date.parse(String(last.start))) / 60_000,
    ),
  );
  const movedEnd = shiftWallClock(movedStart, durationMinutes);
  const patched = await call(
    `/api/demo/events/${last.occurrence_id}`,
    {
      method: "PATCH",
      body: JSON.stringify({
        start: movedStart,
        end: movedEnd,
        location: last.location ?? null,
      }),
    },
    sessionId,
  );
  audit.step(
    "tightened the last leg",
    String(patched.status),
    `${last.occurrence_id} ${last.start} -> ${movedStart}`,
  );
  expect(patched.status, "patch sample event").toBeLessThan(300);
  await runSampleCheck(sessionId);
  day = await call("/api/day", {}, sessionId);
  decisions = day.body?.decisions ?? [];

  expect(decisions.length, "an open decision to resolve").toBeGreaterThan(0);
  const decision = decisions[0];
  const beforeStamp = decisions.map((d) => ({
    id: d.id,
    reason: d.reason,
    journey_key: d.journey_key,
    status: d.status,
    actions: d.allowed_actions,
  }));
  audit.step(
    "open decisions before resolve",
    String(decisions.length),
    JSON.stringify(beforeStamp),
  );
  expect(
    decision.allowed_actions,
    "skip_journey available",
  ).toContain("skip_journey");

  const resolved = await call(
    `/api/decisions/${decision.id}/resolve`,
    { method: "POST", body: JSON.stringify({ action: "skip_journey" }) },
    sessionId,
  );
  audit.step(
    "resolve skip_journey",
    String(resolved.status),
    JSON.stringify(resolved.body),
  );
  expect(resolved.status, "resolve accepted").toBeLessThan(300);

  if (resolved.body?.run_id) {
    const finished = await waitRun(sessionId, resolved.body.run_id as string);
    audit.step(
      "post-resolution run",
      String(finished.body?.run?.status),
      JSON.stringify(finished.body?.run ?? {}).slice(0, 600),
    );
  }

  const afterDay = await call("/api/day", {}, sessionId);
  const after: any[] = afterDay.body?.decisions ?? [];
  const afterStamp = after.map((d) => ({
    id: d.id,
    reason: d.reason,
    journey_key: d.journey_key,
    status: d.status,
    actions: d.allowed_actions,
  }));
  const sameId = after.find((d) => d.id === decision.id);
  if (sameId) {
    audit.high(
      "Skip journey leaves the same decision open",
      `After POST resolve (action=skip_journey) returned success, GET /api/day still lists decision ${decision.id} as ${sameId.status}.`,
      `before=${JSON.stringify(beforeStamp)} after=${JSON.stringify(afterStamp)} resolve=${JSON.stringify(resolved.body)}`,
    );
  } else if (after.length > 0) {
    audit.ok(
      "Skip journey closes the resolved decision",
      `Decision ${decision.id} left the open list; a different decision appears for the next journey. after=${JSON.stringify(afterStamp)}`,
    );
  } else {
    audit.ok(
      "Skip journey clears the decision",
      `Decision ${decision.id} is no longer open. after=${JSON.stringify(afterStamp)}`,
    );
  }

  // Confirm the rendered page agrees with the API.
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    ["glide-sample-session", sessionId],
  );
  await page.goto(BASE, { waitUntil: "networkidle" });
  const cards = await page.locator("article.decision").count();
  audit.step(
    "decision cards rendered",
    String(cards),
    `apiOpen=${after.length}`,
  );
  await audit.shot(page, "after-skip-journey");
  if (cards !== after.length) {
    audit.medium(
      "Rendered decision cards disagree with the API",
      `GET /api/day reports ${after.length} open decision(s) but the page rendered ${cards} decision card(s).`,
      `apiOpen=${after.length} cards=${cards}`,
    );
  }
});

test("unknown API routes should not return the SPA shell", async () => {
  const response = await call("/api/definitely-not-a-route");
  const snippet = response.raw.slice(0, 160).replace(/\s+/g, " ");
  if (response.status === 404) {
    audit.ok(
      "Unknown API route returns 404",
      `GET /api/definitely-not-a-route -> 404 (${response.contentType}).`,
    );
  } else {
    audit.low(
      "Unknown API route returns a success status",
      `GET /api/definitely-not-a-route -> ${response.status} (${response.contentType}). A client cannot tell a bad API path from a real page; clients that probe feature endpoints may treat HTML as data.`,
      `status=${response.status} contentType=${response.contentType} body="${snippet}"`,
    );
  }
});

test("deployed bundle stamp matches the local build", async () => {
  const localIndex = path.resolve(
    process.cwd(),
    "dist",
    "index.html",
  );
  let localAssets: string[] = [];
  let localMtime = "missing";
  if (fs.existsSync(localIndex)) {
    const html = fs.readFileSync(localIndex, "utf8");
    localAssets = Array.from(html.matchAll(/\/assets\/[^"']+/g)).map((m) => m[0]);
    localMtime = fs.statSync(localIndex).mtime.toISOString();
  }
  const live = await fetch(`${BASE}/`);
  const liveHtml = await live.text();
  const liveAssets = Array.from(liveHtml.matchAll(/\/assets\/[^"']+/g)).map(
    (m) => m[0],
  );
  const cacheHeaders = {
    cacheControl: live.headers.get("cache-control"),
    age: live.headers.get("age"),
    etag: live.headers.get("etag"),
    via: live.headers.get("via"),
  };
  audit.step(
    "bundle stamp",
    liveAssets[0] ?? "none",
    `local=${localAssets[0] ?? "none"} localMtime=${localMtime} headers=${JSON.stringify(cacheHeaders)}`,
  );
  if (!localAssets.length || !liveAssets.length) {
    audit.medium(
      "Could not read a bundle stamp",
      "Local dist/index.html or the live index.html had no /assets/ reference.",
      `local=${JSON.stringify(localAssets)} live=${JSON.stringify(liveAssets)}`,
    );
  } else if (
    liveAssets.some((asset) => localAssets.includes(asset))
  ) {
    audit.ok(
      "Deployed bundle matches the local build",
      `Live ${liveAssets[0]} equals local ${localAssets[0]}.`,
    );
  } else {
    audit.medium(
      "Deployed bundle differs from the local build",
      "The live site serves a different asset than frontend/dist; findings may describe a build that is no longer deployable from this worktree.",
      `live=${JSON.stringify(liveAssets)} local=${JSON.stringify(localAssets)} localMtime=${localMtime} headers=${JSON.stringify(cacheHeaders)}`,
    );
  }
  if (!cacheHeaders.cacheControl) {
    audit.low(
      "index.html is served without Cache-Control",
      "The entry document has no explicit Cache-Control, so CloudFront's default TTL governs it and a browser or edge cache can keep serving the previous bundle after a deploy.",
      `headers=${JSON.stringify(cacheHeaders)}`,
    );
  }
});

test("Google connect starts at the provider, without credentials", async () => {
  const response = await fetch(`${BASE}/api/auth/google/start`, {
    redirect: "manual",
  });
  const location = response.headers.get("location") ?? "";
  const ok =
    response.status >= 300 &&
    response.status < 400 &&
    location.startsWith("https://accounts.google.com/");
  const detail = `status=${response.status} location=${location.slice(0, 220)}`;
  if (ok) {
    const params = new URL(location).searchParams;
    audit.ok(
      "Connect Google Calendar redirects to Google",
      `redirect_uri=${params.get("redirect_uri")} scope=${params.get("scope")}`,
    );
  } else {
    audit.high(
      "Connect Google Calendar does not reach the provider",
      "The connect link is the only route into the live Google workflow; it did not return a Google redirect.",
      detail,
    );
  }
});

test("sample session creation and read endpoints are reliable under load", async () => {
  // The suite runs while the rest of the team is exercising the same live site,
  // so separate "a transient 5xx" from "the core endpoint is down".
  const attempts: string[] = [];
  let successes = 0;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const response = await call("/api/demo/session", {
      method: "POST",
      body: "{}",
    });
    attempts.push(`create#${attempt}=${response.status}`);
    if (response.status < 300 && response.body?.session?.session_id) {
      successes += 1;
      const sessionId = response.body.session.session_id as string;
      const day = await call("/api/day", {}, sessionId);
      attempts.push(`day=${day.status}`);
      const me = await call("/api/me", {}, sessionId);
      attempts.push(`me=${me.status}`);
    }
    if (attempt < 3) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  }
  if (successes >= 2) {
    audit.ok(
      "Sample session creation succeeds under audit load",
      `${successes}/3 creation attempts succeeded; timeline: ${attempts.join(", ")}.`,
    );
  } else {
    audit.high(
      "Sample session creation fails repeatedly",
      `Only ${successes}/3 creation attempts succeeded on a live site whose first action is "Try a sample day".`,
      attempts.join(", "),
    );
  }
});

test("headline defects: last_run, event validation, API 404 fallback", async () => {
  test.setTimeout(180_000);
  const created = await createSessionWithRetry();
  if (!created.sessionId) {
    audit.high(
      "Could not seed a session for the defect checks",
      "POST /api/demo/session failed after retries.",
      created.seen.join(", "),
    );
    return;
  }
  const sessionId = created.sessionId;
  const queued = await call(
    "/api/runs",
    { method: "POST", body: JSON.stringify({ trigger: "sample" }) },
    sessionId,
  );
  const runId = queued.body?.run_id ?? queued.body?.run?.id;
  const finished = runId
    ? await waitRun(sessionId, runId)
    : { status: 0, body: null, raw: "", contentType: null };
  const dayAfter = await call("/api/day", {}, sessionId);
  const runStatus = finished.body?.run?.status ?? "unknown";
  const lastRun = dayAfter.body?.last_run ?? null;
  audit.step(
    "run vs day.last_run",
    `${runStatus} / last_run=${lastRun ? lastRun.status : "null"}`,
    `run_id=${runId} blocks=${dayAfter.body?.travel_blocks?.length} decisions=${dayAfter.body?.decisions?.length}`,
  );
  if (TERMINAL.has(runStatus) && lastRun === null) {
    audit.high(
      "GET /api/day never reports the finished run",
      `Run ${runId} reached "${runStatus}", but the same session's GET /api/day returned last_run=null. The day view renders its "Last check" line and the CHECK COMPLETE note from last_run, so a successful check is invisible.`,
      `run.status=${runStatus} last_run=${JSON.stringify(lastRun)} blocks=${dayAfter.body?.travel_blocks?.length} decisions=${dayAfter.body?.decisions?.length}`,
    );
  } else if (lastRun) {
    audit.ok(
      "GET /api/day reports the finished run",
      `last_run.status=${lastRun.status} after run ${runId} reached ${runStatus}.`,
    );
  }

  // Event edits with end <= start are ordinary client errors.
  const events = dayAfter.body?.source_events ?? [];
  if (events.length) {
    const target = events[0];
    const zero = await call(
      `/api/demo/events/${target.occurrence_id}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          start: target.start,
          end: target.start,
          location: target.location ?? null,
        }),
      },
      sessionId,
    );
    const backwards = await call(
      `/api/demo/events/${target.occurrence_id}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          start: target.end,
          end: target.start,
          location: target.location ?? null,
        }),
      },
      sessionId,
    );
    const detail = `zero=${zero.status} ${zero.contentType} "${zero.raw.slice(0, 80)}" / backwards=${backwards.status} ${backwards.contentType} "${backwards.raw.slice(0, 80)}"`;
    audit.step("invalid event ranges", "probed", detail);
    if (zero.status >= 500 || backwards.status >= 500) {
      audit.high(
        "Invalid appointment ranges return 5xx",
        "PATCH /api/demo/events with end equal to or before start answers an internal error; the UI's own editor blocks this, but the API contract turns a client mistake into a server error.",
        detail,
      );
    } else {
      audit.ok("Invalid appointment ranges return 4xx", detail);
    }
  }

  // A session the API does not know should 404, not serve the SPA shell.
  const bogus = await call("/api/day", {}, "00000000000000000000000000000000");
  const body = bogus.raw.slice(0, 100).replace(/\s+/g, " ");
  audit.step(
    "unknown session",
    String(bogus.status),
    `${bogus.contentType} "${body}"`,
  );
  if (bogus.status === 404) {
    audit.ok("Unknown session is a 404", `GET /api/day -> 404 (${bogus.contentType})`);
  } else {
    audit.medium(
      "Unknown session is masked by the SPA fallback",
      `GET /api/day with an unknown session returned ${bogus.status} ${bogus.contentType}. CloudFront rewrites 404/403 to 200 index.html, so an API client cannot distinguish a missing session from a successful call.`,
      `status=${bogus.status} contentType=${bogus.contentType} body="${body}"`,
    );
  }
});
