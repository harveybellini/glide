// A6: HTTP API contract, tenant isolation, error surface and infrastructure
// headers, exercised against the deployed CloudFront API (no UI dependency).
//
// Two traps for future readers:
//  1. Sample tenants share fixture identifiers (occ_a, evt_a, ...), so
//     cross-tenant checks must use unique payload markers, not ids.
//  2. CloudFront rewrites several API 404s into index.html with status 200,
//     so a bare `status === 200` is never proof that an API call succeeded.
import { test, type APIRequestContext, type APIResponse } from "@playwright/test";
import { Audit, LIVE_BASE, expect } from "./harness";

const audit = new Audit("a6-api-contract");

test.afterAll(() => {
  audit.write();
});

type Probe = {
  label: string;
  status: number;
  headers: Record<string, string>;
  body: unknown;
  raw: string;
};

const log: Probe[] = [];

// The first 429 seen anywhere in this run, kept so the throttling finding is
// backed by evidence even if the dedicated burst happens to run in a quiet
// window. Throttling depends on the shared per-client quota, so it is
// deliberately reported as "observed under load" rather than as a fixed limit.
let throttleEvidence: {
  label: string;
  path: string;
  body: string;
  retryAfter: string;
  allowOrigin: string;
  xCache: string;
} | null = null;
let throttleSightings = 0;

function pickHeaders(headers: Record<string, string>, keys: string[]) {
  const out: Record<string, string> = {};
  for (const key of keys) if (headers[key]) out[key] = headers[key];
  return out;
}

async function probe(
  request: APIRequestContext,
  label: string,
  method: "get" | "post" | "patch" | "delete" | "options",
  path: string,
  options: Parameters<APIRequestContext["get"]>[1] = {},
): Promise<Probe> {
  const response: APIResponse =
    method === "options"
      ? await request.fetch(path, { ...(options as object), method: "OPTIONS" })
      : await request[method](path, options as never);
  const headers = response.headers();
  const raw = await response.text();
  let body: unknown = raw;
  try {
    body = JSON.parse(raw);
  } catch {
    /* keep raw text */
  }
  const entry: Probe = { label, status: response.status(), headers, body, raw };
  if (response.status() === 429) {
    throttleSightings += 1;
    throttleEvidence ??= {
      label,
      path,
      body: raw.slice(0, 200),
      retryAfter: headers["retry-after"] ?? "(none)",
      allowOrigin: headers["access-control-allow-origin"] ?? "(none)",
      xCache: headers["x-cache"] ?? "(none)",
    };
  }
  log.push(entry);
  audit.step(
    `${method.toUpperCase()} ${path}`,
    `${response.status()} · ${headers["content-type"] ?? "no content-type"}`,
    JSON.stringify({
      label,
      body: raw.slice(0, 300),
      headers: pickHeaders(headers, [
        "cache-control",
        "access-control-allow-origin",
        "x-content-type-options",
        "strict-transport-security",
        "server",
        "via",
        "x-cache",
      ]),
    }),
  );
  return entry;
}

const isHtml = (entry: Probe) => (entry.headers["content-type"] ?? "").includes("text/html");
const isJsonSuccess = (entry: Probe) =>
  entry.status === 200 && (entry.headers["content-type"] ?? "").includes("application/json");
const bodyText = (entry: Probe) => entry.raw;

// The deployed API throttles bursts aggressively (429, no Retry-After, and the
// throttled response drops the CORS headers). Retry with backoff so a shared
// quota does not masquerade as a functional defect, but keep every attempt in
// the evidence log.
async function probeWithBackoff(
  request: APIRequestContext,
  label: string,
  method: "get" | "post" | "patch" | "delete" | "options",
  path: string,
  options: Parameters<APIRequestContext["get"]>[1] = {},
  attempts = 5,
): Promise<Probe> {
  let last: Probe | null = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    last = await probe(request, attempt === 1 ? label : `${label} (retry ${attempt})`, method, path, options);
    if (last.status !== 429) return last;
    if (attempt < attempts) await sleep(3_000 * attempt);
  }
  audit.step(`still throttled: ${label}`, String(last?.status ?? "?"), `after ${attempts} attempts`);
  return last as Probe;
}

function looksLikeLeak(entry: Probe) {
  const text = bodyText(entry).toLowerCase();
  return (
    text.includes("traceback") ||
    text.includes("botocore") ||
    text.includes("dynamodb") ||
    text.includes("amazonaws.com") ||
    text.includes("client_secret") ||
    text.includes("refresh_token") ||
    text.includes("access_token")
  );
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// POST /api/demo/session is throttled at the API edge (429, no Retry-After),
// so the suite backs off instead of treating fleet load as a product defect.
async function createSession(
  request: APIRequestContext,
  label = "create sample session",
  attempts = 6,
): Promise<string> {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const created = await probe(
      request,
      attempt === 1 ? label : `${label} (retry ${attempt})`,
      "post",
      "/api/demo/session",
      { data: {} },
    );
    if (created.status === 201) {
      return (created.body as { session: { session_id: string } }).session.session_id;
    }
    if (created.status === 429 && attempt < attempts) {
      await sleep(4_000 * attempt);
      continue;
    }
    expect(created.status, `POST /api/demo/session should return 201 (attempt ${attempt})`).toBe(201);
  }
  throw new Error("unreachable: createSession exhausted its attempts");
}

const S = (id: string) => ({ headers: { "X-Glide-Session": id } });

type Day = {
  date: string;
  source_events: { occurrence_id: string; location: string | null; start: string; end: string }[];
  travel_blocks: { journey_key: string }[];
  decisions: { id: string; allowed_actions: string[] }[];
};

async function getDay(request: APIRequestContext, session: string, label: string): Promise<Day> {
  const day = await probe(request, label, "get", "/api/day", S(session));
  expect(day.status, `${label} should return 200`).toBe(200);
  expect(isHtml(day), `${label} must not be the HTML fallback`).toBe(false);
  return day.body as Day;
}

test("A6.1 landing, auth status, headers and secret hygiene", async ({ request }) => {
  const landing = await probe(request, "landing", "get", "/");
  expect(landing.status).toBe(200);
  const headers = landing.headers;

  const cacheControl = headers["cache-control"] ?? "";
  if (/immutable|max-age=(?!0)[0-9]{3,}/.test(cacheControl)) {
    audit.high(
      "index.html is served with a long cache lifetime",
      `Cache-Control: ${cacheControl}. A long-lived index.html keeps visitors on a stale bundle after a deploy.`,
      JSON.stringify(pickHeaders(headers, ["cache-control", "etag", "last-modified"])),
    );
  } else {
    audit.ok("index.html cache policy is safe", `Cache-Control: ${cacheControl || "(none)"}`);
  }

  const missingSecurity = ["x-content-type-options", "strict-transport-security", "content-security-policy"].filter(
    (key) => !headers[key],
  );
  if (missingSecurity.length) {
    audit.low(
      "Missing security headers on the HTML response",
      `Not set: ${missingSecurity.join(", ")}. No CSP leaves an injection bug without a second line of defence.`,
      JSON.stringify(pickHeaders(headers, ["x-content-type-options", "strict-transport-security", "content-security-policy"])),
    );
  } else {
    audit.ok("Security headers present on HTML", "nosniff, HSTS and CSP all set");
  }

  const status = await probe(request, "auth status signed out", "get", "/api/auth/status");
  expect(status.status).toBe(200);
  const keys = Object.keys(status.body as Record<string, unknown>).sort();
  audit.step("auth status keys", keys.join(","));
  if (keys.some((key) => /secret|token|credential/i.test(key))) {
    audit.high("Auth status exposes credential-shaped fields", `keys: ${keys.join(", ")}`, bodyText(status).slice(0, 400));
  } else {
    audit.ok("Auth status exposes no credential fields", keys.join(", "));
  }

  const health = await probe(request, "health", "get", "/api/health");
  audit.step("health endpoint", String(health.status), bodyText(health).slice(0, 120));

  const html = await (await request.get("/")).text();
  const assetPaths = Array.from(html.matchAll(/(?:src|href)="([^"]+\.(?:js|css))"/g)).map((m) => m[1]);
  audit.step("hashed assets in index.html", String(assetPaths.length), assetPaths.join(", "));
  for (const path of assetPaths.slice(0, 4)) {
    const asset = await probe(request, `asset ${path}`, "get", path);
    const assetCache = asset.headers["cache-control"] ?? "(none)";
    if (!/max-age=[0-9]{4,}|immutable/.test(assetCache)) {
      audit.low(
        "Hashed asset has no long-lived Cache-Control",
        `${path} returned Cache-Control: ${assetCache}. Content-hashed files can be cached forever; without it visitors re-download the bundle.`,
      );
    }
    if (/client_secret/.test(bodyText(asset))) {
      audit.high("Secret-looking string in the shipped bundle", `Deployed bundle ${path} matched a credential pattern.`);
    }
  }
  audit.dump("a6-probes", log);
});

test("A6.2 session header handling never 5xx", async ({ request }) => {
  const session = await createSession(request);
  await getDay(request, session, "day with a valid session");

  const noHeader = await probe(request, "day without any session", "get", "/api/day");
  if (noHeader.status === 200 && !isHtml(noHeader)) {
    audit.blocker(
      "API answers without any session",
      "GET /api/day returned JSON 200 with no X-Glide-Session header, so an anonymous caller read a tenant's day.",
      bodyText(noHeader).slice(0, 400),
    );
  } else {
    audit.ok("Day requires a session", `status ${noHeader.status}`);
  }

  const garbage = await probe(request, "day with a garbage session", "get", "/api/day", S("not-a-real-session"));
  if (garbage.status >= 500) {
    audit.high("Garbage session id causes a server error", `status ${garbage.status}`, bodyText(garbage).slice(0, 300));
  } else {
    audit.ok("Garbage session id rejected without a 5xx", `status ${garbage.status}`);
  }

  const longHeader = await probe(request, "day with a 10k-char session", "get", "/api/day", S("x".repeat(10_000)));
  if (longHeader.status >= 500) {
    audit.high("Oversized session header causes a server error", `status ${longHeader.status}`);
  } else {
    audit.ok("Oversized session header handled", `status ${longHeader.status}`);
  }

  const whitespace = await probe(request, "day with a whitespace session", "get", "/api/day", S(" "));
  if (whitespace.status >= 500) {
    audit.medium("Whitespace session header causes a server error", `status ${whitespace.status}`);
  } else {
    audit.ok("Whitespace session header handled", `status ${whitespace.status}`);
  }
  audit.dump("a6-session-headers", log);
});

test("A6.3 tenant isolation proven with unique markers", async ({ request }) => {
  const a = await createSession(request, "create session A");
  const b = await createSession(request, "create session B");
  const dayA = await getDay(request, a, "day as A");
  const dayB = await getDay(request, b, "day as B");
  const eventA = dayA.source_events[0];
  audit.step(
    "fixture id overlap between sample tenants",
    `${eventA.occurrence_id} vs ${dayB.source_events[0].occurrence_id}`,
    "Sample tenants share fixture ids by design, so isolation is proven with unique payload markers.",
  );

  const marker = `MARKER-A-${Date.now()}`;
  const patch = await probe(request, "A marks its own event location", "patch", `/api/demo/events/${eventA.occurrence_id}`, {
    ...S(a),
    data: { start: eventA.start, end: eventA.end, location: marker },
  });
  expect(patch.status, "A should be able to edit its own event").toBe(200);

  const dayAfterA = await getDay(request, a, "A reads back its marker");
  if (!(dayAfterA.source_events[0].location ?? "").includes(marker)) {
    audit.high("Tenant cannot see its own edit", "Session A's location marker was not returned by A's own day read.");
  } else {
    audit.ok("Tenant sees its own edit", marker);
  }

  const dayAfterB = await getDay(request, b, "B reads its day after A's edit");
  if (dayAfterB.source_events.some((event) => (event.location ?? "").includes(marker))) {
    audit.blocker(
      "Tenant B sees tenant A's edit",
      `Session B's day contains A's unique marker (${marker}); sample sessions are not isolated.`,
      JSON.stringify(dayAfterB.source_events.map((event) => event.location)),
    );
  } else {
    audit.ok("Tenant B does not see tenant A's edit", `marker ${marker} absent`);
  }

  await probe(request, "A reads its day again", "get", "/api/day", S(a));
  const bAgain = await getDay(request, b, "B reads its day again");
  if (bAgain.source_events.some((event) => (event.location ?? "").includes(marker))) {
    audit.blocker("Cross-tenant cache leak", "A repeat read of B's day returned A's marker.");
  } else {
    audit.ok("No cross-tenant cache leak", "interleaved reads stayed isolated");
  }

  const runA = await probeWithBackoff(request, "A queues a run", "post", "/api/runs", {
    ...S(a),
    data: { trigger: "sample" },
  });
  const runId = (runA.body as { run_id?: string }).run_id;
  if (runId) {
    const crossRun = await probe(request, "B polls A's run id", "get", `/api/runs/${runId}`, S(b));
    if (isJsonSuccess(crossRun)) {
      audit.high(
        "Tenant B can read tenant A's run",
        "GET /api/runs/<A's run> with B's session returned JSON 200.",
        bodyText(crossRun).slice(0, 300),
      );
    } else {
      audit.ok(
        "Tenant B cannot read A's run id",
        `status ${crossRun.status}, content-type ${crossRun.headers["content-type"] ?? "?"}`,
      );
    }
  }
  audit.dump("a6-isolation", log);
});

test("A6.4 request validation: 4xx not 5xx, no internal leakage", async ({ request }) => {
  const session = await createSession(request);
  const day = await getDay(request, session, "day for validation probes");
  const eventId = day.source_events[0].occurrence_id;
  const event = day.source_events[0];

  const cases: { label: string; method: "get" | "post" | "patch" | "delete"; path: string; options?: object }[] = [
    { label: "malformed JSON body", method: "post", path: "/api/runs", options: { headers: { "X-Glide-Session": session, "Content-Type": "application/json" }, data: "{not json" } },
    { label: "wrong type for padding", method: "patch", path: "/api/settings", options: { ...S(session), data: { padding_minutes: "abc" } } },
    { label: "padding above max (61)", method: "patch", path: "/api/settings", options: { ...S(session), data: { padding_minutes: 61 } } },
    { label: "padding below min (-1)", method: "patch", path: "/api/settings", options: { ...S(session), data: { padding_minutes: -1 } } },
    { label: "unknown settings field", method: "patch", path: "/api/settings", options: { ...S(session), data: { bogus_field: 1 } } },
    { label: "invalid time zone", method: "patch", path: "/api/settings", options: { ...S(session), data: { time_zone: "Mars/Olympus" } } },
    { label: "invalid notification email", method: "patch", path: "/api/settings", options: { ...S(session), data: { notification_email: "not-an-email" } } },
    { label: "event end before start", method: "patch", path: `/api/demo/events/${eventId}`, options: { ...S(session), data: { start: "2026-09-11T12:00:00Z", end: "2026-09-11T11:00:00Z" } } },
    { label: "event end equal to start", method: "patch", path: `/api/demo/events/${eventId}`, options: { ...S(session), data: { start: "2026-09-11T12:00:00Z", end: "2026-09-11T12:00:00Z" } } },
    { label: "event with a non-ISO start", method: "patch", path: `/api/demo/events/${eventId}`, options: { ...S(session), data: { start: "tomorrow", end: "2026-09-11T13:00:00Z" } } },
    { label: "unknown decision resolve", method: "post", path: "/api/decisions/nope/resolve", options: { ...S(session), data: { action: "skip_journey" } } },
    { label: "wrong method on /api/day", method: "delete", path: "/api/day", options: S(session) },
    { label: "wrong method on /api/runs", method: "get", path: "/api/runs", options: S(session) },
    { label: "trailing slash on /api/day", method: "get", path: "/api/day/", options: S(session) },
    { label: "query injection in place search", method: "get", path: "/api/places/search?query=%27%20OR%201%3D1--", options: S(session) },
    { label: "empty place search", method: "get", path: "/api/places/search?query=", options: S(session) },
    { label: "oversized event location", method: "patch", path: `/api/demo/events/${eventId}`, options: { ...S(session), data: { start: event.start, end: event.end, location: "L".repeat(50_000) } } },
    { label: "xss-shaped event location", method: "patch", path: `/api/demo/events/${eventId}`, options: { ...S(session), data: { start: event.start, end: event.end, location: "<img src=x onerror=alert(1)>" } } },
  ];

  for (const item of cases) {
    const result = await probeWithBackoff(request, item.label, item.method, item.path, item.options as never);
    if (result.status >= 500) {
      audit.high(
        `5xx on a malformed request: ${item.label}`,
        `${item.method.toUpperCase()} ${item.path} returned ${result.status} (${result.headers["content-type"] ?? "?"}). Client input errors must be 4xx.`,
        bodyText(result).slice(0, 300),
      );
    }
    if (looksLikeLeak(result)) {
      audit.high(
        `Internal detail leaked on: ${item.label}`,
        "Response body matched internal/secret patterns.",
        bodyText(result).slice(0, 300),
      );
    }
  }
  audit.dump("a6-validation", log);
});

test("A6.5 API 404s are rewritten into index.html by CloudFront", async ({ request }) => {
  const session = await createSession(request);
  const observed: { label: string; status: number; contentType: string; bodyStart: string }[] = [];

  const cases: { label: string; method: "get" | "post" | "patch"; path: string; options?: object }[] = [
    { label: "unknown API route", method: "get", path: "/api/definitely-not-a-route", options: S(session) },
    { label: "unknown run id", method: "get", path: "/api/runs/00000000-0000-0000-0000-000000000000", options: S(session) },
    { label: "unknown occurrence id", method: "patch", path: "/api/demo/events/does-not-exist", options: { ...S(session), data: { start: "2026-09-11T12:00:00Z", end: "2026-09-11T13:00:00Z" } } },
    { label: "path traversal id", method: "patch", path: "/api/demo/events/..%2F..%2Fetc%2Fpasswd", options: { ...S(session), data: { start: "2026-09-11T12:00:00Z", end: "2026-09-11T13:00:00Z" } } },
    { label: "garbage session", method: "get", path: "/api/day", options: S("does-not-exist") },
    { label: "unknown decision resolve", method: "post", path: "/api/decisions/garbage/resolve", options: { ...S(session), data: { action: "skip_journey" } } },
  ];

  for (const item of cases) {
    const result = await probe(request, `fallback probe: ${item.label}`, item.method, item.path, item.options as never);
    observed.push({
      label: item.label,
      status: result.status,
      contentType: result.headers["content-type"] ?? "(none)",
      bodyStart: result.raw.slice(0, 80).replace(/\s+/g, " "),
    });
  }
  audit.dump("a6-fallback", observed);

  const htmlAsOk = observed.filter((entry) => entry.status === 200 && entry.contentType.includes("text/html"));
  if (htmlAsOk.length) {
    audit.medium(
      "API 404s are rewritten to index.html with status 200",
      `${htmlAsOk.map((entry) => entry.label).join(", ")} returned HTML 200 instead of a JSON 404. Any API client — including Glide's own frontend when a sample session expires or a run id is stale — receives HTML where it expects JSON, so ` +
        "`response.json()` throws a parse error and the user sees a raw syntax error instead of \"session expired\". Fix: exclude /api/* from the SPA fallback, or return a JSON 404 body from the custom error response.",
      JSON.stringify(observed, null, 2),
    );
  } else {
    audit.ok("API 404s stay JSON", JSON.stringify(observed));
  }

  for (const entry of observed.filter((item) => !item.contentType.includes("text/html"))) {
    audit.step(`kept as JSON: ${entry.label}`, `${entry.status} ${entry.contentType}`, entry.bodyStart);
  }
  audit.dump("a6-fallback-probes", log);
});

test("A6.6 run lifecycle, decision actions and double-resolve", async ({ request }) => {
  const session = await createSession(request);
  const first = await probeWithBackoff(request, "queue run #1", "post", "/api/runs", {
    ...S(session),
    data: { trigger: "sample" },
  });
  expect(first.status).toBe(202);
  const firstId = (first.body as { run_id: string }).run_id;

  const quickly = await probeWithBackoff(request, "queue run #2 immediately", "post", "/api/runs", {
    ...S(session),
    data: { trigger: "sample" },
  });
  audit.step("second queue status", String(quickly.status), bodyText(quickly).slice(0, 160));

  const terminal = new Set(["completed", "needs_input", "failed", "superseded", "paused"]);
  let finalStatus = "";
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const poll = await request.get(`/api/runs/${firstId}`, S(session));
    const payload = (await poll.json()) as { run: { status: string } };
    finalStatus = payload.run.status;
    if (terminal.has(finalStatus)) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!terminal.has(finalStatus)) {
    audit.high("Run never reached a terminal state", `After 60s run #1 was still "${finalStatus}".`);
  } else {
    audit.ok("Run reaches a terminal state", finalStatus);
  }

  const replay = await probe(request, "replay finished run", "get", `/api/runs/${firstId}`, S(session));
  if (isJsonSuccess(replay) && (replay.body as { run: { status: string } }).run.status !== finalStatus) {
    audit.medium(
      "Replayed run reports a different status",
      `First read "${finalStatus}", replay read "${(replay.body as { run: { status: string } }).run.status}".`,
    );
  } else {
    audit.ok("Finished run is stable on replay", `status ${replay.status}`);
  }

  const decisions = await probe(request, "list decisions", "get", "/api/decisions", S(session));
  expect(decisions.status).toBe(200);
  const list = (decisions.body as { decisions: { id: string; allowed_actions: string[] }[] }).decisions;
  audit.step("decisions after run", String(list.length), list.map((d) => d.allowed_actions.join("|")).join(" ; "));
  expect(list.length, "the sample run should raise a decision").toBeGreaterThan(0);

  const target = list[0];
  const disallowed = ["skip_journey", "treat_as_virtual", "correct_location", "keep_manual_edit", "replace_with_plan", "recreate_journey"].find(
    (action) => !target.allowed_actions.includes(action),
  ) ?? "not_a_real_action";
  const bad = await probeWithBackoff(request, "resolve with a disallowed action", "post", `/api/decisions/${target.id}/resolve`, {
    ...S(session),
    data: { action: disallowed },
  });
  if (isJsonSuccess(bad)) {
    audit.high(
      "Disallowed decision action accepted",
      `allowed_actions=${JSON.stringify(target.allowed_actions)} but "${disallowed}" returned JSON 200.`,
      bodyText(bad).slice(0, 300),
    );
  } else {
    audit.ok("Disallowed decision action rejected", `action "${disallowed}" -> ${bad.status}`);
  }

  // add_anyway is a sample-supported resolution: answering it would consume the
  // decision this audit still wants to replay, so only actions the sample path
  // genuinely cannot honour are probed here.
  const advertised = target.allowed_actions.filter(
    (action) => action !== "skip_journey" && action !== "add_anyway",
  );
  if (advertised.length) {
    const attempt = await probeWithBackoff(request, "run an advertised non-sample action", "post", `/api/decisions/${target.id}/resolve`, {
      ...S(session),
      data: { action: advertised[0] },
    });
    if (attempt.status === 400) {
      audit.nit(
        "Sample decisions advertise actions the sample path rejects",
        `allowed_actions includes ${JSON.stringify(advertised)} but the sample session rejects "${advertised[0]}" with 400: ${bodyText(attempt).slice(0, 200)}. The UI gates these on a connected account, so users are not misled — the contract is just wider than the sample implementation.`,
      );
    }
  }

  const ok1 = await probeWithBackoff(request, "resolve skip_journey (first)", "post", `/api/decisions/${target.id}/resolve`, {
    ...S(session),
    data: { action: "skip_journey" },
  });
  const ok2 = await probeWithBackoff(request, "resolve skip_journey (replay)", "post", `/api/decisions/${target.id}/resolve`, {
    ...S(session),
    data: { action: "skip_journey" },
  });
  if (ok1.status >= 500 || ok2.status >= 500) {
    audit.high("Double-resolve causes a server error", `first ${ok1.status}, second ${ok2.status}`);
  } else {
    audit.ok(
      "Replaying a decision resolution is safe",
      `first ${ok1.status} (${bodyText(ok1).slice(0, 90)}), replay ${ok2.status} (${bodyText(ok2).slice(0, 90)})`,
    );
  }

  const other = await createSession(request, "create a second tenant");
  const cross = await probeWithBackoff(request, "second tenant resolves this decision", "post", `/api/decisions/${target.id}/resolve`, {
    ...S(other),
    data: { action: "skip_journey" },
  });
  if (isJsonSuccess(cross)) {
    audit.blocker("Cross-tenant decision resolve succeeded", "A second session resolved another tenant's decision with JSON 200.");
  } else {
    audit.ok(
      "Cross-tenant decision resolve rejected",
      `status ${cross.status}, content-type ${cross.headers["content-type"] ?? "?"}`,
    );
  }
  audit.dump("a6-runs", log);
});

test("A6.7 CORS, caching, throttling and origin hygiene", async ({ request }) => {
  const session = await createSession(request);
  const foreignOrigin = "https://evil.example";

  const preflight = await probeWithBackoff(request, "preflight from a foreign origin", "options", "/api/day", {
    headers: { Origin: foreignOrigin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-glide-session" },
  });
  const allowOrigin = preflight.headers["access-control-allow-origin"];
  audit.step("foreign preflight", String(preflight.status), `access-control-allow-origin=${allowOrigin ?? "(none)"}`);
  if (allowOrigin && (allowOrigin === "*" || allowOrigin === foreignOrigin)) {
    audit.high("Permissive CORS for a foreign origin", `OPTIONS /api/day from ${foreignOrigin} allowed: ${allowOrigin}.`);
  } else {
    audit.ok("Foreign-origin preflight is refused", `access-control-allow-origin=${allowOrigin ?? "(none)"}`);
  }

  const ownOrigin = await probeWithBackoff(request, "preflight from the site origin", "options", "/api/day", {
    headers: { Origin: LIVE_BASE, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-glide-session" },
  });
  if (ownOrigin.headers["access-control-allow-origin"] !== LIVE_BASE) {
    audit.medium(
      "Site origin not allowed by CORS",
      `OPTIONS /api/day from ${LIVE_BASE} returned access-control-allow-origin: ${ownOrigin.headers["access-control-allow-origin"] ?? "(none)"}.`,
    );
  } else {
    audit.ok("Site-origin preflight allowed", `status ${ownOrigin.status}`);
  }

  const apiCache = await probe(request, "day response cache headers", "get", "/api/day", S(session));
  const cc = apiCache.headers["cache-control"] ?? "";
  if (!/no-store|no-cache|private/.test(cc)) {
    audit.medium(
      "Authenticated API responses carry no cache directives",
      `GET /api/day returned Cache-Control: ${cc || "(none)"} (x-cache: ${apiCache.headers["x-cache"] ?? "(none)"}). Tenant data should be explicitly no-store so no intermediary or browser heuristic-caches one person's day.`,
      JSON.stringify(pickHeaders(apiCache.headers, ["cache-control", "age", "x-cache", "etag", "vary"])),
    );
  } else {
    audit.ok("API responses are non-cacheable", `Cache-Control: ${cc}`);
  }

  const statuses: Record<string, number> = {};
  for (let i = 0; i < 30; i += 1) {
    const response = await request.get("/api/health");
    const key = String(response.status());
    statuses[key] = (statuses[key] ?? 0) + 1;
  }
  audit.step("30-request burst status distribution", JSON.stringify(statuses));
  audit.dump("a6-throttle-evidence", {
    sightingsThisRun: throttleSightings,
    burstDistribution: statuses,
    first: throttleEvidence,
  });
  if (throttleSightings > 0) {
    if (statuses["429"]) audit.ok("Throttling observed", `burst produced ${statuses["429"]} x 429`);
    const first = throttleEvidence!;
    audit.medium(
      "Throttled responses carry no Retry-After and drop the CORS headers",
      `The API returned 429 ${throttleSightings} time(s) during this run; first on ${first.path} ` +
        `(body ${JSON.stringify(first.body)}) with retry-after=${first.retryAfter} and access-control-allow-origin=${first.allowOrigin} (x-cache: ${first.xCache}). ` +
        "A browser caller cannot read the throttled response, cannot distinguish \"busy\" from \"blocked\", and gets no backoff hint, so Glide's own UI shows a generic request failure for a throttled Recheck or sample start. " +
        "Fix: attach CORS headers and Retry-After to throttle responses, return a `detail` string, and retry with backoff in the client. " +
        "Note the limit is a shared per-client quota: a slow 12-request burst (150ms spacing) succeeded 12/12, while unspaced bursts trip it.",
      JSON.stringify(first),
    );
  } else {
    audit.nit(
      "No throttling observed in a 30-request burst",
      `Status distribution: ${JSON.stringify(statuses)}, and no 429 was seen anywhere in this run. Throttling is real but depends on the shared per-client quota (other audits hit it repeatedly), so it is not reproducible in every window.`,
    );
  }

  try {
    const badHost = await probe(request, "forged Host header", "get", "/api/auth/status", { headers: { Host: "attacker.example" } });
    audit.step("forged host probe", String(badHost.status), bodyText(badHost).slice(0, 120));
  } catch (error) {
    audit.ok("A forged Host header cannot reach the origin", String(error).split("\n")[0].slice(0, 160));
  }
  audit.dump("a6-infra", log);
});

test("A6.8 session-creation throttling: threshold, shape and recovery", async ({ request }) => {
  // Sit out the previous bucket so the measurement is not polluted by the
  // audit fleet's own load. The API refills quickly (see the retry ladder in
  // createSession), so a short pause is enough.
  await sleep(20_000);

  const attemptStatuses: { attempt: number; status: number; ms: number; body: string; retryAfter: string }[] = [];
  let firstThrottledAt: number | null = null;
  for (let attempt = 1; attempt <= 12; attempt += 1) {
    const started = Date.now();
    const response = await request.post("/api/demo/session", { data: {} });
    const body = (await response.text()).slice(0, 120);
    attemptStatuses.push({
      attempt,
      status: response.status(),
      ms: Date.now() - started,
      body,
      retryAfter: response.headers()["retry-after"] ?? "(none)",
    });
    if (response.status() === 429 && firstThrottledAt === null) firstThrottledAt = attempt;
    await sleep(150);
  }
  audit.dump("a6-throttle-attempts", attemptStatuses);
  audit.step(
    "session-creation burst",
    JSON.stringify(attemptStatuses.map((entry) => entry.status)),
    `first 429 at attempt ${firstThrottledAt ?? "none"}; retry-after ${attemptStatuses[0]?.retryAfter}`,
  );

  if (firstThrottledAt !== null) {
    const throttled = attemptStatuses.find((entry) => entry.status === 429);
    audit.medium(
      "Starting a sample day is rate-limited per client",
      `A sequential burst of POST /api/demo/session hit 429 at attempt ${firstThrottledAt} ` +
        `(body ${throttled?.body}, retry-after ${throttled?.retryAfter}). A judge who reloads or restarts the sample a few times, ` +
        "or several judges behind one NAT, can therefore see the demo refuse to start. The frontend surfaces only " +
        "`Request failed with 429` (api.ts falls back to statusText because the body uses `message`, not `detail`) and never retries. " +
        "Fix: send Retry-After, return a `detail` string, and back off in createSampleSession.",
      JSON.stringify(attemptStatuses.slice(Math.max(0, firstThrottledAt - 3), firstThrottledAt + 2)),
    );
  } else {
    audit.ok("Session creation was not throttled during a slow burst", JSON.stringify(attemptStatuses.map((e) => e.status)));
  }

  const healthDuring = await request.get("/api/health");
  audit.step("health during the throttle window", String(healthDuring.status()));

  await sleep(15_000);
  const recovered = await request.post("/api/demo/session", { data: {} });
  if (recovered.status() === 201) {
    audit.ok("Throttle recovers after a short pause", "session creation succeeded again after 15s");
  } else {
    audit.high(
      "Throttle does not recover after a short pause",
      `POST /api/demo/session still returned ${recovered.status()} 15s after the burst; a judge could be locked out for longer than the demo takes to evaluate.`,
      (await recovered.text()).slice(0, 200),
    );
  }
  audit.dump("a6-throttle", log);
});
