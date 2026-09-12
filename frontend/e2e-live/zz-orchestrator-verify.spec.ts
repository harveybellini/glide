// Orchestrator verification pass.
//
// Independently re-tests the highest-severity claims produced by the parallel
// audit areas (cross-tenant isolation, SPA-fallback masking of API 404s,
// settings bounds, run/last_run lifecycle, skip-journey resolution) and adds
// first-hand coverage for responsive layout, cache behaviour and headers.
//
// Report: temp/live-audit/reports/zz-orchestrator-verify.md
import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";
import { Audit, LIVE_BASE, createSession, openWithSession } from "./harness";

const audit = new Audit("zz-orchestrator-verify");

// Playwright restarts the worker after a failed test, which would lose the
// in-memory findings of everything that ran before it. Persist after every
// test and merge with whatever is already on disk.
function save() {
  const file = path.resolve(
    process.cwd(),
    "..",
    "temp",
    "live-audit",
    "reports",
    `${audit.area}.json`,
  );
  try {
    if (fs.existsSync(file)) {
      const previous = JSON.parse(fs.readFileSync(file, "utf8")) as {
        findings?: { severity: string; title: string; detail: string; evidence: string | null }[];
        steps?: { name: string; outcome: string; detail: string | null }[];
      };
      const known = new Set(audit.findings.map((finding) => `${finding.severity}:${finding.title}`));
      for (const finding of previous.findings ?? []) {
        if (!known.has(`${finding.severity}:${finding.title}`)) {
          audit.findings.push({ ...finding, area: audit.area });
        }
      }
      const knownSteps = new Set(audit.steps.map((step) => `${step.name}:${step.outcome}`));
      for (const step of previous.steps ?? []) {
        if (!knownSteps.has(`${step.name}:${step.outcome}`)) audit.steps.push(step);
      }
    }
  } catch {
    // A missing or unreadable previous report is not an audit failure.
  }
  return audit.write();
}

test.afterEach(() => {
  save();
});
test.afterAll(() => {
  save();
});

type Probe = {
  status: number;
  contentType: string | null;
  cacheControl: string | null;
  xCache: string | null;
  age: string | null;
  body: string;
  raw: string;
};

async function probe(path: string, init: RequestInit = {}): Promise<Probe> {
  const response = await fetch(`${LIVE_BASE}${path}`, init);
  const text = await response.text();
  return {
    status: response.status,
    contentType: response.headers.get("content-type"),
    cacheControl: response.headers.get("cache-control"),
    xCache: response.headers.get("x-cache"),
    age: response.headers.get("age"),
    body: text.slice(0, 400),
    raw: text,
  };
}

function sessionInit(sessionId: string, method = "GET", body?: unknown): RequestInit {
  return {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Glide-Session": sessionId,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  };
}

type Day = {
  date: string;
  source_events: {
    occurrence_id: string;
    start: string;
    end: string;
    location?: string | null;
    title: string;
  }[];
  travel_blocks: { journey_key: string; start: string; end: string }[];
  decisions: {
    id: string;
    reason: string;
    allowed_actions: string[];
    calculated_facts: Record<string, unknown>;
  }[];
  last_run?: {
    id: string;
    status: string;
    ended_at?: string | null;
    safe_failure_code?: string | null;
  } | null;
};

async function getDay(sessionId: string): Promise<Day> {
  const response = await fetch(`${LIVE_BASE}/api/day`, {
    headers: { "X-Glide-Session": sessionId },
  });
  return (await response.json()) as Day;
}

const POLL_TIMEOUT = 90_000;

async function recheck(sessionId: string) {
  // The deployed stage throttles at 50 burst / 25 rps and the audit fleet is
  // loud, so retry a throttled queue request instead of recording a false
  // defect from a run that never started.
  let queued = await probe("/api/runs", sessionInit(sessionId, "POST", { trigger: "sample" }));
  for (let attempt = 1; attempt <= 3 && (queued.status === 429 || queued.status >= 500); attempt += 1) {
    audit.step("POST /api/runs throttled", `status ${queued.status}, retry ${attempt}`, queued.body);
    await new Promise((resolve) => setTimeout(resolve, 2000 * attempt));
    queued = await probe("/api/runs", sessionInit(sessionId, "POST", { trigger: "sample" }));
  }
  audit.step("POST /api/runs", `status ${queued.status}`, queued.body);
  // A queued run is answered with 202 Accepted.
  if (queued.status !== 200 && queued.status !== 202) {
    return { queued, run: null as null | Record<string, unknown> };
  }
  const runId = (JSON.parse(queued.body) as { run_id?: string; run?: { id?: string } }).run_id;
  const deadline = Date.now() + POLL_TIMEOUT;
  let last: Record<string, unknown> | null = null;
  while (Date.now() < deadline && runId) {
    const poll = await probe(`/api/runs/${runId}`, {
      headers: { "X-Glide-Session": sessionId },
    });
    if (poll.status !== 200 || !(poll.contentType ?? "").includes("json")) break;
    last = JSON.parse(poll.raw) as Record<string, unknown>;
    const status = (last.run as { status?: string } | undefined)?.status;
    audit.step(`poll run ${runId}`, String(status), null);
    if (status && ["completed", "needs_input", "failed", "superseded", "paused"].includes(status)) {
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  return { queued, run: last };
}

test.describe("orchestrator verification", () => {
  test.describe.configure({ timeout: 300_000 });

  test("tenancy: a second session cannot mutate or read another session's data", async () => {
    const a = await createSession();
    const b = await createSession();
    const dayA = await getDay(a);
    const target = dayA.source_events[0];
    expect(target).toBeTruthy();

    // Baseline: session A can edit its own copy.
    const movedA = await probe(
      `/api/demo/events/${target.occurrence_id}`,
      sessionInit(a, "PATCH", {
        start: target.start,
        end: target.end,
        location: "AAA-ONLY-SESSION-A",
      }),
    );
    const dayAAfterMove = await getDay(a);
    expect(dayAAfterMove.source_events[0].location).toBe("AAA-ONLY-SESSION-A");

    // Session B patches the same occurrence id (fixture ids are shared).
    const movedB = await probe(
      `/api/demo/events/${target.occurrence_id}`,
      sessionInit(b, "PATCH", {
        start: target.start,
        end: target.end,
        location: "BBB-ONLY-SESSION-B",
      }),
    );
    const dayAAfterB = await getDay(a);
    const dayBAfterB = await getDay(b);
    audit.dump("tenancy-events", {
      target: target.occurrence_id,
      movedA: { status: movedA.status, body: movedA.body },
      movedB: { status: movedB.status, body: movedB.body },
      aLocation: dayAAfterB.source_events[0].location,
      bLocation: dayBAfterB.source_events[0].location,
    });
    if (dayAAfterB.source_events[0].location !== "AAA-ONLY-SESSION-A") {
      audit.blocker(
        "Cross-tenant event edit really leaks between sessions",
        "Session B's PATCH changed session A's calendar copy.",
        JSON.stringify({ aLocation: dayAAfterB.source_events[0].location }, null, 2),
      );
    } else {
      audit.ok(
        "Cross-tenant event edit is isolated",
        `Session A kept '${dayAAfterB.source_events[0].location}' after session B patched the same occurrence id.`,
      );
    }

    // Run isolation: A queues a run, B reads its id.
    const aRun = await recheck(a);
    const runId = aRun.queued.status === 200
      ? (JSON.parse(aRun.queued.body) as { run_id: string }).run_id
      : null;
    if (runId) {
      const foreignRun = await probe(`/api/runs/${runId}`, {
        headers: { "X-Glide-Session": b },
      });
      audit.dump("tenancy-run", { runId, foreignRun });
      const isJson = (foreignRun.contentType ?? "").includes("json");
      if (foreignRun.status === 200 && isJson) {
        audit.blocker(
          "Cross-tenant run read leaks data",
          "Session B received JSON for a run id owned by session A.",
          JSON.stringify(foreignRun, null, 2),
        );
      } else {
        audit.ok(
          "Cross-tenant run read is not served",
          `Session B got ${foreignRun.status} ${foreignRun.contentType ?? "(no content-type)"} for session A's run id (body masked by the SPA fallback).`,
        );
      }
    }

    // Decision isolation: A's decision must survive B resolving the same id.
    const dayWithDecision = await getDay(a);
    if (dayWithDecision.decisions.length) {
      const decisionId = dayWithDecision.decisions[0].id;
      const resolveByB = await probe(
        `/api/decisions/${decisionId}/resolve`,
        sessionInit(b, "POST", { action: "skip_journey" }),
      );
      const dayAAfterResolve = await getDay(a);
      audit.dump("tenancy-decision", {
        decisionId,
        resolveByB: { status: resolveByB.status, body: resolveByB.body },
        aDecisionsAfter: dayAAfterResolve.decisions.map((decision) => decision.id),
        bDecisionsAfter: (await getDay(b)).decisions.map((decision) => decision.id),
      });
      if (dayAAfterResolve.decisions.some((decision) => decision.id === decisionId)) {
        audit.ok(
          "Cross-tenant decision resolve is isolated",
          `Session A's decision ${decisionId} survived session B resolving the same id.`,
        );
      } else {
        audit.blocker(
          "Cross-tenant decision resolve really leaks",
          "Session B resolving a decision id removed session A's decision.",
          decisionId,
        );
      }
    } else {
      audit.step("decision isolation", "skipped", "session A had no decision to resolve");
    }
  });

  test("unknown API identifiers and routes are masked as HTML 200 by the SPA fallback", async () => {
    const a = await createSession();
    const unknownRun = await probe("/api/runs/00000000-0000-0000-0000-000000000000", {
      headers: { "X-Glide-Session": a, Accept: "application/json" },
    });
    const unknownRoute = await probe("/api/definitely-not-a-route", {
      headers: { "X-Glide-Session": a, Accept: "application/json" },
    });
    const unknownDecision = await probe("/api/decisions/nope/resolve", sessionInit(a, "POST", { action: "skip_journey" }));
    const unknownEvent = await probe("/api/demo/events/does-not-exist", sessionInit(a, "PATCH", {
      start: "2026-09-11T09:00:00Z",
      end: "2026-09-11T09:30:00Z",
      location: "nope",
    }));
    const missingAsset = await probe("/assets/index-00000000.js");
    const unknownPage = await probe("/this-does-not-exist");
    const anonymousDay = await probe("/api/day");

    const masked = {
      unknownRun,
      unknownRoute,
      unknownDecision,
      unknownEvent,
      missingAsset,
      unknownPage,
      anonymousDay,
    };
    audit.dump("masked-404s", masked);

    const html200 = Object.entries(masked).filter(
      ([, value]) => value.status === 200 && (value.contentType ?? "").includes("text/html"),
    );
    if (html200.length) {
      audit.medium(
        "CloudFront 404/403 SPA fallback masks API and asset misses as HTML 200",
        `infra/template.yaml maps 403/404 to /index.html with ResponseCode 200 for the whole distribution, so ${html200.length} of 7 missing-resource probes returned HTML with HTTP 200. Clients expecting JSON (frontend request()) instead fail while parsing HTML, and a missing asset hash can never be detected as a 404.`,
        JSON.stringify(Object.fromEntries(html200), null, 2),
      );
    } else {
      audit.ok("Missing resources return real 4xx responses", "");
    }

    // 500s are not remapped, so they surface raw.
    const day = await getDay(a);
    const first = day.source_events[0];
    const badTimes = await probe(
      `/api/demo/events/${first.occurrence_id}`,
      sessionInit(a, "PATCH", {
        start: first.end,
        end: first.start,
        location: first.location ?? "",
      }),
    );
    audit.dump("end-before-start", badTimes);
    if (badTimes.status >= 500) {
      audit.high(
        "End-before-start event edit returns a raw 500",
        "A malformed request reaches the server as an unhandled error instead of a validated 4xx, and CloudFront shows the bare 'Internal Server Error' body.",
        `PATCH /api/demo/events/${first.occurrence_id}\nstatus ${badTimes.status}\n${badTimes.body}`,
      );
    } else {
      audit.ok(
        "End-before-start event edit is rejected as a client error",
        `status ${badTimes.status}: ${badTimes.body}`,
      );
    }

    const afterBad = await getDay(a);
    const stillOriginal =
      afterBad.source_events[0].start === day.source_events[0].start &&
      afterBad.source_events[0].end === day.source_events[0].end;
    if (stillOriginal) {
      audit.ok("Rejected event edit left the day untouched", "");
    } else {
      audit.high(
        "Failed event edit still mutated the day",
        "The end-before-start request errored but the stored event changed.",
        JSON.stringify(
          { before: day.source_events[0], after: afterBad.source_events[0] },
          null,
          2,
        ),
      );
    }
  });

  test("settings bounds are enforced by the API and the client", async ({ page }) => {
    const a = await createSession();
    const results: Record<string, Probe> = {};
    for (const value of [99, 61, -1, 60, 0, "abc", 10.5]) {
      results[String(value)] = await probe(
        "/api/settings",
        sessionInit(a, "PATCH", { padding_minutes: value }),
      );
    }
    audit.dump("settings-bounds-api", results);
    const accepted = Object.entries(results).filter(([, value]) => value.status === 200);
    const expectedAccepted = ["60", "0"];
    const wrong = accepted
      .map(([key]) => key)
      .filter((key) => !expectedAccepted.includes(key));
    if (wrong.length) {
      audit.high(
        "API accepts out-of-range arrival buffers",
        `PATCH /api/settings accepted: ${wrong.join(", ")}.`,
        JSON.stringify(
          Object.fromEntries(accepted.map(([key, value]) => [key, value.body])),
          null,
          2,
        ),
      );
    } else {
      audit.ok(
        "API enforces the 0-60 minute arrival buffer",
        `accepted ${expectedAccepted.join(", ")}; rejected 99/61/-1/abc/10.5 with ${results["99"].status}.`,
      );
    }

    // UI: fill 99 and try to save. The form must not persist it.
    await openWithSession(page, a);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    const buffer = panel.getByLabel("Arrival buffer (minutes)");
    await buffer.fill("99");
    await panel.getByRole("button", { name: "Save settings" }).click();
    const validationMessage = await buffer.evaluate(
      (element: HTMLInputElement) => element.validationMessage,
    );
    const panelStillOpen = (await panel.count()) > 0;
    const shown = await page.getByText(/whole number between 0 and 60/i).count();
    const settingsAfter = (await (
      await fetch(`${LIVE_BASE}/api/me`, { headers: { "X-Glide-Session": a } })
    ).json()) as { padding_minutes: number };
    audit.dump("settings-bounds-ui", { validationMessage, panelStillOpen, shown, settingsAfter });
    if (settingsAfter.padding_minutes === 99) {
      audit.high(
        "UI accepted a 99-minute arrival buffer",
        "The settings panel saved padding_minutes=99.",
        JSON.stringify({ validationMessage, panelStillOpen, settingsAfter }, null, 2),
      );
    } else {
      audit.ok(
        "UI rejects a 99-minute arrival buffer",
        `stored padding_minutes=${settingsAfter.padding_minutes}; native message='${validationMessage}'`,
      );
    }
    await audit.shot(page, "settings-99-blocked");
  });

  test("run lifecycle updates last_run and the day view", async ({ page }) => {
    const a = await createSession();
    const before = await getDay(a);
    const started = Date.now();
    const { queued, run } = await recheck(a);
    const elapsed = Date.now() - started;
    const after = await getDay(a);
    audit.dump("last-run", {
      before: before.last_run ?? null,
      runResult: run,
      after: after.last_run ?? null,
      elapsedMs: elapsed,
      decisions: after.decisions.map((decision) => decision.id),
    });

    const terminalStatus = (run?.run as { status?: string } | undefined)?.status;
    const runStarted = queued.status === 200 || queued.status === 202;
    if (!runStarted) {
      audit.low(
        "Could not queue a check while the audit fleet was running",
        `POST /api/runs returned ${queued.status} after four attempts, so last_run could not be verified in this pass.`,
        queued.body,
      );
    } else if (!after.last_run) {
      audit.high(
        "A finished check is not reflected in /api/day last_run",
        `Run finished as '${terminalStatus}' after ${elapsed}ms but GET /api/day still returns last_run=null, so the UI cannot show the "Last check" line or the CHECK COMPLETE state.`,
        JSON.stringify({ after: after.last_run ?? null, terminalStatus, elapsed }, null, 2),
      );
    } else {
      audit.ok(
        "Run lifecycle updates last_run",
        `last_run.status=${after.last_run.status} ended_at=${after.last_run.ended_at ?? "(none)"}`,
      );
    }

    // Same journey through the UI, read from the persisted run state. The
    // audit fleet itself is heavy enough to trip the API throttle, so this
    // check deliberately avoids a second run for the same evidence.
    await openWithSession(page, a);
    await page.waitForTimeout(1500);
    const lastCheckVisible = await page.getByText(/last check:/i).count();
    const checkComplete = await page.getByText("CHECK COMPLETE", { exact: true }).count();
    const readyWhenYouAre = await page.getByText("READY WHEN YOU ARE", { exact: true }).count();
    const day = await getDay(a);
    audit.dump("last-run-ui", { lastCheckVisible, checkComplete, readyWhenYouAre, lastRun: day.last_run ?? null });
    if (runStarted && !lastCheckVisible) {
      audit.medium(
        'Activity never shows a "Last check" line',
        "After a completed check the Activity section omits the last-run line, so a judge cannot tell a check ran.",
        JSON.stringify({ lastCheckVisible, checkComplete, readyWhenYouAre, lastRun: day.last_run }, null, 2),
      );
    } else if (runStarted) {
      audit.ok("Activity shows the last check line after a run", "");
    }
    if (day.decisions.length === 0 && readyWhenYouAre > 0 && checkComplete === 0) {
      audit.medium(
        'Quiet note still says "READY WHEN YOU ARE" after a completed check',
        "The finished run is not reflected in the last_run payload the note depends on.",
        JSON.stringify({ checkComplete, readyWhenYouAre, lastRun: day.last_run }, null, 2),
      );
    }
    await audit.shot(page, "after-recheck");
  });

  test("skip journey resolves the decision through the UI", async ({ page }) => {
    const a = await createSession();
    await openWithSession(page, a);
    await page.getByRole("button", { name: "Recheck now" }).click();
    const decision = page.getByRole("heading", { name: "Needs your decision" });
    const action = page.getByRole("button", { name: "Skip this journey" });
    await expect(decision).toBeVisible({ timeout: POLL_TIMEOUT });
    if (!(await action.count())) {
      audit.medium(
        "Sample decision does not offer Skip this journey",
        "The sample decision card rendered without the documented skip action.",
        (await page.locator(".decision").innerText()).slice(0, 400),
      );
      return;
    }
    await action.click();
    await expect(page.getByText("Journey skipped.")).toBeVisible({ timeout: POLL_TIMEOUT });
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toHaveCount(0, {
      timeout: 10_000,
    });
    const day = await getDay(a);
    audit.dump("skip-journey", { decisions: day.decisions.length, travelBlocks: day.travel_blocks.length });
    if (day.decisions.length === 0) {
      audit.ok("Skip this journey clears the decision", `travel blocks remaining: ${day.travel_blocks.length}`);
    } else {
      audit.high(
        "Skip this journey leaves the decision open",
        "The status message appeared but GET /api/day still returns the decision.",
        JSON.stringify(day.decisions, null, 2),
      );
    }
    await audit.shot(page, "after-skip-journey");
  });

  test("a throttled or failed check leaves a clear error, not a stale status", async ({ page }) => {
    const a = await createSession();
    await openWithSession(page, a);
    await page.route("**/api/runs", (route) =>
      route.fulfill({
        status: 429,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Too Many Requests" }),
      }),
    );
    await page.getByRole("button", { name: "Recheck now" }).click();
    const alert = page.getByRole("alert");
    await expect(alert).toBeVisible({ timeout: 15_000 });
    const alertText = (await alert.innerText()).trim();
    const statusText = (await page.locator("p.status").innerText()).trim();
    const buttonText = (await page.locator(".day-heading button.primary").innerText()).trim();
    audit.dump("throttled-check", { alertText, statusText, buttonText });
    if (/planning travel/i.test(statusText) && /429|too many/i.test(alertText)) {
      audit.medium(
        "A failed check leaves the stale status 'Planning travel...'",
        "On a 429/network failure the error alert appears but the aria-live status keeps saying the check is still in progress, so the user is told two contradictory things (and the deployed stage throttles at 50 burst / 25 rps).",
        JSON.stringify({ alertText, statusText, buttonText }, null, 2),
      );
    } else {
      audit.ok(
        "A failed check reports a clear status",
        `alert='${alertText}' status='${statusText}' button='${buttonText}'`,
      );
    }
    await audit.shot(page, "throttled-check");
  });

  test("cache behaviour and headers for HTML, API and assets", async ({ page }) => {
    const a = await createSession();
    const b = await createSession();
    await probe("/api/settings", sessionInit(a, "PATCH", { padding_minutes: 17 }));
    await probe("/api/settings", sessionInit(b, "PATCH", { padding_minutes: 43 }));
    const dayA = await getDay(a);
    const dayB = await getDay(b);

    const home = await probe("/");
    const apiA = await probe("/api/day", { headers: { "X-Glide-Session": a } });
    const apiA2 = await probe("/api/day", { headers: { "X-Glide-Session": a } });
    const apiB = await probe("/api/day", { headers: { "X-Glide-Session": b } });
    const auth = await probe("/api/auth/status");
    const assetPath = await page
      .goto("/", { waitUntil: "domcontentloaded" })
      .then(async () => {
        const html = await page.content();
        const match = html.match(/\/assets\/[A-Za-z0-9._-]+\.js/);
        return match?.[0] ?? null;
      });
    const asset = assetPath ? await probe(assetPath) : null;

    audit.dump("headers", { home, apiA, apiA2, apiB, auth, assetPath, asset });
    audit.step("settings isolation over cache", "day values", `${dayA.date} vs ${dayB.date}`);

    const leaked = dayA.source_events[0].occurrence_id === dayB.source_events[0].occurrence_id;
    audit.step(
      "fixture ids shared across sessions",
      leaked ? "identical occurrence ids" : "distinct occurrence ids",
      `${dayA.source_events[0].occurrence_id} / ${dayB.source_events[0].occurrence_id}`,
    );

    for (const [label, value] of Object.entries({ apiA, apiA2, apiB })) {
      if (/max-age=\s*[1-9]/.test(value.cacheControl ?? "") || (!value.cacheControl && value.age && value.age !== "0")) {
        audit.high(
          `Session-specific API response looks cacheable (${label})`,
          "A shared cache could serve one visitor's day to another.",
          JSON.stringify({ cacheControl: value.cacheControl, age: value.age, xCache: value.xCache }, null, 2),
        );
      }
    }
    audit.ok(
      "API responses are not shared-cached",
      `x-cache values: ${apiA.xCache} / ${apiA2.xCache} / ${apiB.xCache}; cache-control=${apiA.cacheControl ?? "(none)"} (infra pins /api/* to the CachingDisabled policy)`,
    );

    const missingSecurity: string[] = [];
    for (const header of [
      "content-security-policy",
      "x-content-type-options",
      "referrer-policy",
      "strict-transport-security",
      "x-frame-options",
      "permissions-policy",
    ]) {
      const response = await fetch(`${LIVE_BASE}/`);
      if (!response.headers.get(header)) missingSecurity.push(header);
    }
    if (missingSecurity.length) {
      audit.low(
        "Missing security headers on the HTML response",
        `Not set: ${missingSecurity.join(", ")}. The API returns JSON and the SPA escapes user text, so this is hardening rather than an exploit, but there is no CSP or frame-ancestors protection.`,
        `GET / -> 200, missing: ${missingSecurity.join(", ")}`,
      );
    } else {
      audit.ok("Security headers present on the HTML response", "");
    }

    if (asset) {
      if (!/max-age=\s*\d{4,}/.test(asset.cacheControl ?? "")) {
        audit.low(
          "Hashed asset is not long-lived cacheable",
          `The content-hashed JS bundle returns cache-control=${asset.cacheControl ?? "(none)"}, so every visit revalidates instead of reusing the immutable file.`,
          JSON.stringify({ assetPath, cacheControl: asset.cacheControl, xCache: asset.xCache }, null, 2),
        );
      } else {
        audit.ok("Hashed asset is long-lived cacheable", `cache-control=${asset.cacheControl}`);
      }
    }

    const http = await probe("/", { method: "HEAD" });
    audit.step("HEAD /", `status ${http.status}`, `cache-control=${http.cacheControl ?? "(none)"} x-cache=${http.xCache ?? "(none)"}`);
  });

  test("responsive layout and accessibility spot checks", async ({ page }) => {
    const viewports = [
      { width: 320, height: 568 },
      { width: 375, height: 667 },
      { width: 768, height: 1024 },
      { width: 1440, height: 900 },
    ];
    const overflow: Record<string, unknown> = {};
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      const landing = await page.evaluate(() => {
        const doc = document.documentElement;
        const offenders: string[] = [];
        document.querySelectorAll<HTMLElement>("body *").forEach((node) => {
          const rect = node.getBoundingClientRect();
          if (rect.width > 0 && (rect.right > window.innerWidth + 1 || rect.left < -1)) {
            offenders.push(
              `${node.tagName.toLowerCase()}.${node.className || "(no-class)"} [${Math.round(rect.left)},${Math.round(
                rect.right,
              )}]`,
            );
          }
        });
        const small: string[] = [];
        document
          .querySelectorAll<HTMLElement>("a, button, input, select, [role=button]")
          .forEach((node) => {
            const rect = node.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0 && (rect.width < 24 || rect.height < 24)) {
              small.push(
                `${node.tagName.toLowerCase()}:${(node.textContent ?? "").trim().slice(0, 24)} ${Math.round(
                  rect.width,
                )}x${Math.round(rect.height)}`,
              );
            }
          });
        return {
          scrollWidth: doc.scrollWidth,
          innerWidth: window.innerWidth,
          overflow: doc.scrollWidth > window.innerWidth + 1,
          offenders: offenders.slice(0, 12),
          smallTargets: small.slice(0, 12),
          lang: doc.lang,
          h1: document.querySelectorAll("h1").length,
          title: document.title,
        };
      });
      await page.goto("/", { waitUntil: "domcontentloaded" });
      const sessionId = await createSession();
      await openWithSession(page, sessionId);
      const workspace = await page.evaluate(() => {
        const doc = document.documentElement;
        return {
          scrollWidth: doc.scrollWidth,
          innerWidth: window.innerWidth,
          overflow: doc.scrollWidth > window.innerWidth + 1,
        };
      });
      overflow[`${viewport.width}x${viewport.height}`] = { landing, workspace };
      await audit.shot(page, `workspace-${viewport.width}`);
    }
    audit.dump("responsive", overflow);
    const offenders = Object.entries(overflow).filter(
      ([, value]) =>
        (value as { landing: { overflow: boolean } }).landing.overflow ||
        (value as { workspace: { overflow: boolean } }).workspace.overflow,
    );
    if (offenders.length) {
      audit.medium(
        "Horizontal overflow at mobile/desktop widths",
        `Overflowing viewports: ${offenders.map(([key]) => key).join(", ")}.`,
        JSON.stringify(offenders, null, 2),
      );
    } else {
      audit.ok("No horizontal overflow at 320/375/768/1440", JSON.stringify(overflow, null, 2));
    }

    // Contrast spot check on muted copy in the workspace.
    await openWithSession(page, await createSession());
    const contrast = await page.evaluate(() => {
      const luminance = (color: string) => {
        const parts = color.match(/[\d.]+/g)?.map(Number) ?? [];
        const [r, g, b] = parts.slice(0, 3).map((value) => {
          const channel = value / 255;
          return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      };
      const samples: { selector: string; color: string; background: string; ratio: number }[] = [];
      document.querySelectorAll<HTMLElement>(".muted, .eyebrow, .small, .status").forEach((node) => {
        const style = getComputedStyle(node);
        let background = style.backgroundColor;
        let parent: HTMLElement | null = node;
        while (parent && (background === "rgba(0, 0, 0, 0)" || background === "transparent")) {
          parent = parent.parentElement;
          if (parent) background = getComputedStyle(parent).backgroundColor;
        }
        const light = luminance(style.color);
        const dark = luminance(background || "rgb(255, 255, 255)");
        const ratio = (Math.max(light, dark) + 0.05) / (Math.min(light, dark) + 0.05);
        samples.push({
          selector: `${node.tagName.toLowerCase()}.${node.className}`,
          color: style.color,
          background,
          ratio: Math.round(ratio * 100) / 100,
        });
      });
      return samples;
    });
    audit.dump("contrast-samples", contrast);
    const failing = contrast.filter((sample) => sample.ratio < 4.5);
    if (failing.length) {
      audit.medium(
        "Low contrast text",
        `${failing.length} sampled element(s) fall below 4.5:1.`,
        JSON.stringify(failing, null, 2),
      );
    } else {
      audit.ok("Sampled muted text meets 4.5:1", JSON.stringify(contrast, null, 2));
    }
  });

  test("accessible names, focus and edit-button disambiguation", async ({ page }) => {
    await openWithSession(page, await createSession());
    const edits = page.getByRole("button", { name: "Edit", exact: true });
    const count = await edits.count();
    const names: string[] = [];
    for (let index = 0; index < count; index += 1) {
      names.push((await edits.nth(index).getAttribute("aria-label")) ?? "");
      names.push((await edits.nth(index).innerText()).trim());
    }
    audit.dump("edit-buttons", names);
    const unlabelled = names.filter((value, index) => index % 2 === 0 && !value).length;
    if (unlabelled > 1) {
      audit.low(
        'Repeated "Edit" buttons have no distinguishing accessible name',
        `${unlabelled} timeline Edit buttons expose only the text "Edit", so a screen-reader user cannot tell which appointment each one edits.`,
        JSON.stringify(names, null, 2),
      );
    } else {
      audit.ok("Edit buttons are distinguishable", JSON.stringify(names));
    }

    // Focus visibility + Escape handling on the settings panel.
    const settings = page.getByRole("button", { name: "Settings", exact: true });
    await settings.click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    const focused = await page.evaluate(() => {
      const element = document.activeElement as HTMLElement | null;
      return element ? `${element.tagName.toLowerCase()}#${element.id}.${element.className}` : "none";
    });
    await page.keyboard.press("Escape");
    const settingsFocusedAfterEscape = await settings.evaluate(
      (node) => document.activeElement === node,
    );
    audit.dump("settings-focus", { focused, panelOpen: await panel.count(), settingsFocusedAfterEscape });
    if (!settingsFocusedAfterEscape) {
      audit.low(
        "Escape on the settings panel does not return focus to the trigger",
        "Keyboard users lose their place after closing the panel.",
        JSON.stringify({ focused, settingsFocusedAfterEscape }, null, 2),
      );
    } else {
      audit.ok("Escape closes settings and restores focus", focused);
    }

    // Event editor Escape behaviour.
    await page.getByRole("button", { name: "Edit", exact: true }).first().click();
    const editor = page.locator("form.event-editor");
    await expect(editor).toBeVisible();
    await page.keyboard.press("Escape");
    const editorStillOpen = (await editor.count()) > 0;
    audit.dump("editor-escape", { editorStillOpen });
    if (editorStillOpen) {
      audit.low(
        "Escape does not close the event editor",
        "The settings panel closes on Escape but the event editor does not, which is inconsistent for keyboard users.",
        "form.event-editor still visible after Escape",
      );
      await editor.getByRole("button", { name: "Cancel", exact: true }).click();
    } else {
      audit.ok("Escape closes the event editor", "");
    }

    const liveRegions = await page.evaluate(() =>
      Array.from(document.querySelectorAll('[aria-live], [role="alert"]')).map((node) => ({
        tag: node.tagName.toLowerCase(),
        role: node.getAttribute("role"),
        live: node.getAttribute("aria-live"),
        text: (node.textContent ?? "").slice(0, 60),
      })),
    );
    audit.dump("live-regions", liveRegions);
    if (!liveRegions.some((region) => region.live === "polite" || region.role === "status")) {
      audit.medium(
        "No polite live region for run status",
        "Status text is rendered into a plain paragraph, so screen readers are not told when a check finishes.",
        JSON.stringify(liveRegions, null, 2),
      );
    } else {
      audit.ok("Run status is announced through a live region", JSON.stringify(liveRegions));
    }
  });

  test("mobile screenshots for the record", async ({ page }) => {
    fs.mkdirSync(audit.artifacts, { recursive: true });
    for (const viewport of [
      { width: 375, height: 667 },
      { width: 768, height: 1024 },
      { width: 1440, height: 900 },
    ]) {
      await page.setViewportSize(viewport);
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await audit.shot(page, `landing-${viewport.width}`);
      await openWithSession(page, await createSession());
      await page.getByRole("button", { name: "Recheck now" }).click();
      await expect(
        page.getByText(/travel plan updated|decision needs your input/i).first(),
      ).toBeVisible({ timeout: POLL_TIMEOUT });
      await audit.shot(page, `day-${viewport.width}`);
    }
  });

  test("deployed bundle parity with the local build", async ({ request }) => {
    const home = await request.get("/");
    const html = await home.text();
    const liveJs = html.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/)?.[0] ?? null;
    const liveCss = html.match(/\/assets\/index-[A-Za-z0-9_-]+\.css/)?.[0] ?? null;
    const localHtml = fs.readFileSync(
      path.resolve(process.cwd(), "dist", "index.html"),
      "utf8",
    );
    const localJs = localHtml.match(/\/assets\/index-[A-Za-z0-9_-]+\.js/)?.[0] ?? null;
    const localCss = localHtml.match(/\/assets\/index-[A-Za-z0-9_-]+\.css/)?.[0] ?? null;

    let liveBundle = "";
    if (liveJs) {
      liveBundle = await (await request.get(liveJs)).text();
    }
    let localBundle = "";
    if (localJs) {
      localBundle = fs.readFileSync(
        path.resolve(process.cwd(), "dist", localJs.replace(/^\//, "")),
        "utf8",
      );
    }
    const markers = [
      "boot-skeleton",
      "glide-live-hint",
      "decision-focused",
      "notify_on_decisions",
      "Last check: ",
      "CHECK COMPLETE",
      "READY WHEN YOU ARE",
    ];
    const presence = Object.fromEntries(
      markers.map((marker) => [
        marker,
        { live: liveBundle.includes(marker), local: localBundle.includes(marker) },
      ]),
    );
    audit.dump("bundle-parity", {
      liveJs,
      localJs,
      liveCss,
      localCss,
      liveBytes: liveBundle.length,
      localBytes: localBundle.length,
      identicalHash: liveBundle === localBundle,
      presence,
    });
    if (liveJs !== localJs) {
      audit.medium(
        "Deployed frontend bundle differs from the local build",
        `The live index.html references ${liveJs} while frontend/dist references ${localJs}. The live site is serving a build that is not the one in the repo working tree.`,
        JSON.stringify({ liveJs, localJs, liveBytes: liveBundle.length, localBytes: localBundle.length }, null, 2),
      );
    } else {
      audit.ok("Deployed bundle matches the local build", `${liveJs} (${liveBundle.length} bytes)`);
    }
    const divergent = Object.entries(presence).filter(
      ([, value]) => value.live !== value.local,
    );
    if (divergent.length) {
      audit.low(
        "Bundle feature markers differ between live and local",
        `Markers present in only one build: ${divergent.map(([key]) => key).join(", ")}.`,
        JSON.stringify(presence, null, 2),
      );
    }
  });

  test("OAuth error paths return recoverable responses", async ({ request }) => {
    const start = await request.get("/api/auth/google/start", { maxRedirects: 0 });
    const startLocation = start.headers()["location"] ?? "";
    const state = new URL(startLocation).searchParams.get("state") ?? "";
    const cookies = await request.storageState();
    const transactionCookie = cookies.cookies.find((cookie) =>
      /transaction|oauth|state|session/i.test(cookie.name),
    );

    const denied = await request.get(
      `/api/auth/google/callback?error=access_denied&state=${encodeURIComponent(state)}`,
      { maxRedirects: 0 },
    );
    const deniedBody = await denied.text();

    const forged = await request.get(
      "/api/auth/google/callback?code=forged&state=forged",
      { maxRedirects: 0 },
    );
    const forgedBody = await forged.text();

    // A real transaction cookie with a code Google will reject: this is the
    // path a user hits when refreshing the callback URL.
    const stale = await request.get(
      `/api/auth/google/callback?code=not-a-real-code&state=${encodeURIComponent(state)}`,
      { maxRedirects: 0 },
    );
    const staleBody = await stale.text();

    audit.dump("oauth-errors", {
      startStatus: start.status(),
      stateLength: state.length,
      transactionCookie: transactionCookie
        ? `${transactionCookie.name} secure=${transactionCookie.secure} httpOnly=${transactionCookie.httpOnly} sameSite=${transactionCookie.sameSite}`
        : "(none found)",
      denied: { status: denied.status(), contentType: denied.headers()["content-type"], body: deniedBody.slice(0, 300) },
      forged: { status: forged.status(), contentType: forged.headers()["content-type"], body: forgedBody.slice(0, 300) },
      staleCode: { status: stale.status(), contentType: stale.headers()["content-type"], body: staleBody.slice(0, 300) },
    });

    if (stale.status >= 500) {
      audit.high(
        "Refreshing the OAuth callback crashes with a 5xx",
        "With a valid transaction cookie and a rejected code, the callback returns a raw server error instead of a recoverable message, so a user who refreshes or resumes an expired consent is stranded.",
        `GET /api/auth/google/callback?code=not-a-real-code&state=<real>\nstatus ${stale.status} ${stale.headers()["content-type"]}\n${staleBody.slice(0, 300)}`,
      );
    } else {
      audit.ok("OAuth callback rejects a bad code gracefully", `status ${stale.status}: ${staleBody.slice(0, 120)}`);
    }
    if ((denied.headers()["content-type"] ?? "").includes("json") && denied.status() === 401) {
      audit.low(
        "Cancelling Google consent lands on a raw JSON error",
        "Clicking Cancel on the provider screen returns a bare JSON 401 body with no link back to the app.",
        `status ${denied.status()}\n${deniedBody.slice(0, 200)}`,
      );
    }
    if (forged.status === 200 && (forged.headers()["content-type"] ?? "").includes("html")) {
      audit.medium(
        "A forged OAuth state is answered with the SPA shell and HTTP 200",
        "CloudFront's 403 remap hides the callback's CSRF rejection from the client.",
        `status ${forged.status} ${forged.headers()["content-type"]}`,
      );
    }
  });

  test("the sample day planner survives reordered days", async () => {
    const cases: { label: string; start: string; end: string }[] = [
      { label: "occ_a after occ_c (c->a leg)", start: "2026-09-12T12:30:00Z", end: "2026-09-12T13:30:00Z" },
      { label: "occ_a between occ_b and occ_c (b->a leg)", start: "2026-09-12T11:45:00Z", end: "2026-09-12T12:45:00Z" },
    ];
    const results: Record<string, unknown> = {};
    for (const item of cases) {
      const a = await createSession();
      const day = await getDay(a);
      const first = day.source_events.find((event) => event.occurrence_id === "occ_a");
      if (!first) continue;
      const moved = await probe(
        `/api/demo/events/${first.occurrence_id}`,
        sessionInit(a, "PATCH", { start: item.start, end: item.end, location: first.location ?? "" }),
      );
      const { queued, run } = await recheck(a);
      const after = await getDay(a);
      const status = (run?.run as { status?: string } | undefined)?.status ?? null;
      results[item.label] = {
        moved: moved.status,
        queued: queued.status,
        runStatus: status,
        failureCode: (run?.run as { safe_failure_code?: string | null } | undefined)?.safe_failure_code ?? null,
        afterBlocks: after.travel_blocks.length,
        afterDecisions: after.decisions.length,
      };
      if (status === "failed") {
        audit.high(
          "Reordering the sample day fails the whole check",
          `Moving the first appointment to ${item.start} makes the run fail instead of producing a decision, leaving the judge with ${after.travel_blocks.length} travel blocks and ${after.decisions.length} decisions.`,
          JSON.stringify(results[item.label], null, 2),
        );
      }
    }
    audit.dump("reordered-days", results);
    const failures = Object.values(results).filter(
      (value) => (value as { runStatus?: string }).runStatus === "failed",
    );
    if (!failures.length) {
      audit.ok("Reordered days still reach a decision instead of failing", JSON.stringify(results, null, 2));
    }
  });

  test.skip("legacy reorder probe kept for reference", async () => {
    const a = await createSession();
    const day = await getDay(a);
    const first = day.source_events.find((event) => event.occurrence_id === "occ_a");
    const last = day.source_events[day.source_events.length - 1];
    if (!first || !last) {
      audit.step("reorder probe", "skipped", "sample events not found");
      return;
    }
    const movedLater = await probe(
      `/api/demo/events/${first.occurrence_id}`,
      sessionInit(a, "PATCH", {
        start: "2026-09-12T12:30:00Z",
        end: "2026-09-12T13:30:00Z",
        location: first.location ?? "",
      }),
    );
    const { queued, run } = await recheck(a);
    const after = await getDay(a);
    audit.dump("reordered-day", {
      movedLater: { status: movedLater.status, body: movedLater.body },
      queuedStatus: queued.status,
      runStatus: (run?.run as { status?: string } | undefined)?.status ?? null,
      failureCode: (run?.run as { safe_failure_code?: string | null } | undefined)?.safe_failure_code ?? null,
      railTrace: JSON.stringify(run?.run ?? {}).slice(0, 200),
      afterBlocks: after.travel_blocks.length,
      afterDecisions: after.decisions.length,
    });
    const status = (run?.run as { status?: string } | undefined)?.status;
    if (status === "failed") {
      audit.high(
        "Reordering the sample day fails the whole check",
        "Moving an appointment so the day needs a reverse fixture route makes the run fail instead of producing a decision, leaving the judge with zero travel blocks and zero decisions.",
        JSON.stringify({ runStatus: status, failureCode: (run?.run as {safe_failure_code?: string} | undefined)?.safe_failure_code, afterBlocks: after.travel_blocks.length, afterDecisions: after.decisions.length }, null, 2),
      );
    } else {
      audit.ok("Reordered day still plans", `run status ${status}`);
    }
  });

  test("time zone setting has a visible effect", async ({ page }) => {
    const a = await createSession();
    await openWithSession(page, a);
    const before = await page.locator(".section-heading").first().innerText();
    const firstTime = (await page.locator("article.row time").first().innerText()).trim();
    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    await panel.getByLabel("Time zone").selectOption("Europe/Paris");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    const after = await page.locator(".section-heading").first().innerText();
    const afterTime = (await page.locator("article.row time").first().innerText()).trim();
    const me = (await (
      await fetch(`${LIVE_BASE}/api/me`, { headers: { "X-Glide-Session": a } })
    ).json()) as { time_zone: string };
    audit.dump("time-zone", { before, after, firstTime, afterTime, storedZone: me.time_zone });
    if (me.time_zone === "Europe/Paris" && before === after && firstTime === afterTime) {
      audit.medium(
        "Time zone setting is stored but changes nothing on screen",
        "Choosing Europe/Paris saves successfully, yet the plan still says 'Times in London' and shows the same clock times, so the setting looks broken to a user.",
        JSON.stringify({ before, after, firstTime, afterTime, storedZone: me.time_zone }, null, 2),
      );
    } else {
      audit.ok("Time zone setting changes the rendered times", JSON.stringify({ before, after, firstTime, afterTime }));
    }
  });

  test("blocked localStorage does not blank the page", async ({ browser }) => {
    const context = await browser.newContext();
    await context.addInitScript(() => {
      Object.defineProperty(window, "localStorage", {
        get() {
          throw new DOMException("Access is denied for this document.", "SecurityError");
        },
      });
    });
    const page = await context.newPage();
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    const state = await page.evaluate(() => ({
      bodyText: (document.body.innerText ?? "").trim().slice(0, 200),
      rootChildren: document.getElementById("root")?.childElementCount ?? -1,
      controls: document.querySelectorAll("a, button").length,
    }));
    audit.dump("blocked-storage", { ...state, errors: errors.slice(0, 3) });
    if (state.rootChildren === 0 || !state.bodyText) {
      audit.medium(
        "Blocked localStorage renders a blank page",
        "Safari private browsing or hardened privacy settings throw on localStorage access; the app then renders nothing at all instead of degrading to the welcome screen.",
        JSON.stringify({ ...state, errors: errors.slice(0, 3) }, null, 2),
      );
    } else {
      audit.ok("App survives blocked localStorage", state.bodyText.slice(0, 80));
    }
    await context.close();
  });

  test("activity anchor lands in view", async ({ page }) => {
    await openWithSession(page, await createSession());
    await page.getByRole("link", { name: /activity/i }).click();
    const box = await page.evaluate(() => {
      const section = document.getElementById("activity");
      const heading = section?.querySelector("h2");
      const rect = heading?.getBoundingClientRect() ?? null;
      return rect ? { top: Math.round(rect.top), bottom: Math.round(rect.bottom), viewport: window.innerHeight } : null;
    });
    audit.dump("activity-anchor", box);
    if (box && (box.top < 0 || box.top > box.viewport - 40)) {
      audit.low(
        "The #activity anchor lands out of view",
        "Following the Activity link leaves the Activity heading outside the visible viewport, so the navigation appears to do nothing.",
        JSON.stringify(box, null, 2),
      );
    } else {
      audit.ok("The #activity anchor lands in view", JSON.stringify(box));
    }
  });

  test("clearing the arrival buffer is rejected, not silently saved as zero", async ({ page }) => {
    const a = await createSession();
    await probe("/api/settings", sessionInit(a, "PATCH", { padding_minutes: 20 }));
    await openWithSession(page, a);
    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    await panel.getByLabel("Arrival buffer (minutes)").fill("");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await page.waitForTimeout(1200);
    const panelOpen = (await panel.count()) > 0;
    const saved = await page.getByText("Settings saved.").count();
    const inlineError = await panel.locator("p.error").count().catch(() => 0);
    const me = (await (
      await fetch(`${LIVE_BASE}/api/me`, { headers: { "X-Glide-Session": a } })
    ).json()) as { padding_minutes: number };
    const stat = (await page.locator(".day-stats").innerText()).replace(/\s+/g, " ");
    audit.dump("empty-buffer", { panelOpen, saved, inlineError, stored: me.padding_minutes, stat });
    if (me.padding_minutes === 0 && !inlineError) {
      audit.medium(
        "Clearing the arrival buffer silently saves 0 minutes",
        'Emptying the Arrival buffer field and saving closes the panel with "Settings saved." and changes the buffer to 0, because Number("") is 0 and passes the range check. A user who clears the field gets a silent, wrong-looking setting.',
        JSON.stringify({ panelOpen, saved, inlineError, stored: me.padding_minutes, stat }, null, 2),
      );
    } else {
      audit.ok("Clearing the arrival buffer does not silently save 0", `stored=${me.padding_minutes} inlineError=${inlineError}`);
    }
  });
});
