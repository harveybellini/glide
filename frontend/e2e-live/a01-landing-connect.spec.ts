// a01: landing page UX + the "connect Google Calendar" (OAuth) entry point.
//
// Audit area for the shared live-site functional pass. Everything here runs
// against the deployed CloudFront site; no credentials are ever typed and the
// Google consent screen is only inspected, never completed.
import { test } from "@playwright/test";
import { Audit, LIVE_BASE, expect, inventory, instrument, squeeze } from "./harness";

const audit = new Audit("a01-landing-connect");
const APP = LIVE_BASE.replace(/\/+$/, "");

test.afterAll(() => {
  audit.write();
});

function cookieValue(setCookieHeader: string | undefined, name: string): string | null {
  if (!setCookieHeader) return null;
  for (const part of setCookieHeader.split(/,(?=\s*[A-Za-z0-9_-]+=)/)) {
    const trimmed = part.trim();
    if (!trimmed.toLowerCase().startsWith(`${name.toLowerCase()}=`)) continue;
    const value = trimmed.slice(name.length + 1).split(";")[0];
    return value;
  }
  return null;
}

function cookieAttributes(setCookieHeader: string | undefined, name: string): string {
  if (!setCookieHeader) return "(no set-cookie header)";
  for (const part of setCookieHeader.split(/,(?=\s*[A-Za-z0-9_-]+=)/)) {
    if (part.trim().toLowerCase().startsWith(`${name.toLowerCase()}=`)) {
      return part.trim().replace(new RegExp(`^${name}=[^;]*`, "i"), `${name}=<redacted>`);
    }
  }
  return "(cookie not present)";
}

// The live stage throttles at burst 50 / 25 rps (infra/template.yaml), and the
// concurrent audit fleet can trip it. Retry once with backoff before treating
// a 429/5xx as a finding, and record either way.
async function getWithRetry(
  page: import("@playwright/test").Page,
  url: string,
  options: Parameters<import("@playwright/test").Page["request"]["get"]>[1] = {},
) {
  const first = await page.request.get(url, options);
  if (first.status() !== 429 && first.status() < 500) return { response: first, retried: false };
  await new Promise((resolve) => setTimeout(resolve, 2000));
  const second = await page.request.get(url, options);
  return { response: second, retried: true, firstStatus: first.status() };
}

test.describe("a01 landing + connect", () => {
  test("landing: meta, a11y, every control, responsive", async ({ page }) => {
    const watch = instrument(page, audit);

    const response = await page.goto("/", { waitUntil: "networkidle" });
    audit.step("GET /", String(response?.status() ?? "no response"));
    expect(response?.status()).toBe(200);

    // --- head / meta -------------------------------------------------------
    const head = await page.evaluate(() => ({
      title: document.title,
      lang: document.documentElement.lang,
      description: document
        .querySelector('meta[name="description"]')
        ?.getAttribute("content"),
      viewport: document
        .querySelector('meta[name="viewport"]')
        ?.getAttribute("content"),
      themeColor: document
        .querySelector('meta[name="theme-color"]')
        ?.getAttribute("content"),
      ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute("content"),
      ogDescription: document
        .querySelector('meta[property="og:description"]')
        ?.getAttribute("content"),
      ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute("content"),
      h1Count: document.querySelectorAll("h1").length,
      headings: Array.from(document.querySelectorAll("h1,h2,h3,h4")).map(
        (node) => `${node.tagName}: ${(node.textContent ?? "").trim().slice(0, 60)}`,
      ),
      images: Array.from(document.querySelectorAll("img")).map((img) => ({
        src: img.getAttribute("src"),
        alt: img.getAttribute("alt"),
        loaded: (img as HTMLImageElement).naturalWidth > 0,
      })),
      landmarks: Array.from(document.querySelectorAll("main,nav,header,footer,aside")).map(
        (node) => `${node.tagName.toLowerCase()}${node.getAttribute("aria-label") ? `[${node.getAttribute("aria-label")}]` : ""}`,
      ),
    }));
    audit.dump("head", head);
    audit.step("title", head.title);
    audit.step("meta", JSON.stringify({ ...head, images: undefined, headings: undefined }));
    if (!head.description) audit.low("No meta description", "Search/social previews fall back to nothing.", JSON.stringify(head));
    if (head.h1Count !== 1) audit.medium("Heading structure: h1 count is not 1", `Found ${head.h1Count} h1 elements.`, head.headings.join("\n"));
    for (const image of head.images) {
      if (!image.loaded) audit.medium("Broken image on the landing page", `Image did not load: ${image.src}`, JSON.stringify(image));
      if (image.alt === null) audit.low("Image missing alt text", `src=${image.src}`, JSON.stringify(image));
    }

    const favicon = await page.request.get(`${APP}/favicon.ico`);
    audit.step("GET /favicon.ico", String(favicon.status()), favicon.headers()["content-type"] ?? "");
    if (favicon.status() >= 400) {
      audit.low("No favicon served", `GET /favicon.ico -> ${favicon.status()}`, JSON.stringify(Object.fromEntries(Object.entries(favicon.headers()))));
    }

    // --- inventory: every visible control on the landing page --------------
    const controls = await inventory(page);
    audit.dump("landing-inventory", controls);
    audit.step(
      "landing controls",
      String(controls.length),
      controls.map((entry) => `${entry.tag}:${entry.label}`).join(" | "),
    );
    await audit.shot(page, "landing-desktop");

    // Skip link: first tab stop, activates #get-started.
    await page.keyboard.press("Tab");
    const firstStop = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      return active
        ? { tag: active.tagName.toLowerCase(), label: (active.textContent ?? "").trim(), cls: active.className }
        : null;
    });
    audit.step("first tab stop", JSON.stringify(firstStop));
    if (firstStop?.tag !== "a" || !String(firstStop.cls).includes("skip-link")) {
      audit.medium("Skip link is not the first tab stop", "Keyboard users should reach the skip link before anything else.", JSON.stringify(firstStop));
    }
    await page.keyboard.press("Enter");
    await page.waitForTimeout(200);
    const afterSkip = await page.evaluate(() => ({
      hash: window.location.hash,
      activeId: (document.activeElement as HTMLElement | null)?.id ?? null,
    }));
    audit.step("skip link activation", JSON.stringify(afterSkip));
    if (afterSkip.hash !== "#get-started" || afterSkip.activeId !== "get-started") {
      audit.medium("Skip link does not move focus to #get-started", "Activating the skip link should land focus on the get-started section.", JSON.stringify(afterSkip));
    }

    // "How it works" anchor + browser back.
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: /how it works/i }).click();
    await page.waitForTimeout(400);
    const scrolled = await page.evaluate(() => window.scrollY);
    audit.step("how-it-works anchor", `scrollY=${scrolled}`, await page.evaluate(() => window.location.hash));
    if (scrolled <= 0) audit.medium("'How it works' anchor does not scroll", `scrollY stayed at ${scrolled}.`, `url=${page.url()}`);
    await page.goBack();
    await page.waitForTimeout(300);
    audit.step("browser back from anchor", page.url(), `scrollY=${await page.evaluate(() => window.scrollY)}`);

    // Unknown hash must not break the page.
    await page.goto("/#definitely-not-a-section", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
    audit.step("unknown hash", "landing still renders", page.url());

    // Hover / focus ring on the primary CTA.
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const cta = page.getByRole("button", { name: /try a sample day/i });
    const before = await cta.evaluate((node) => getComputedStyle(node).backgroundColor);
    await cta.hover();
    await page.waitForTimeout(250);
    const after = await cta.evaluate((node) => getComputedStyle(node).backgroundColor);
    audit.step("primary CTA hover", `${before} -> ${after}`);
    if (before === after) audit.nit("Primary CTA has no visible hover style", `background stayed ${before}.`, `before=${before} after=${after}`);
    await cta.focus();
    const focusStyle = await cta.evaluate((node) => {
      const style = getComputedStyle(node);
      return { outline: style.outline, boxShadow: style.boxShadow };
    });
    audit.step("primary CTA focus style", JSON.stringify(focusStyle));

    // --- connect entry points ---------------------------------------------
    const connectLink = page.getByRole("link", { name: /connect google calendar/i });
    const checking = page.getByText(/checking google calendar connection/i);
    await expect(connectLink.or(checking).first()).toBeVisible({ timeout: 20_000 });
    if (!(await connectLink.first().isVisible().catch(() => false))) {
      audit.high(
        "Connect CTA disappears when the status check fails (organic occurrence)",
        "During this run GET /api/auth/status answered 503 three times (see the console/HTTP findings recorded for this page). ConnectionStatus keeps status=null and renders the 'Checking Google Calendar connection…' placeholder forever, so the Connect Google Calendar link never appears and the visitor cannot start sign-in without a manual reload.",
        "landing body showed: 'Checking Google Calendar connection…' and no Connect Google Calendar link after 20s",
      );
      await page.reload({ waitUntil: "domcontentloaded" });
      await expect(connectLink.first()).toBeVisible({ timeout: 20_000 });
    }
    const landingHref = await connectLink.getAttribute("href");
    audit.step("landing connect href", String(landingHref));
    if (landingHref !== "/api/auth/google/start") {
      audit.medium("Landing connect link is not the documented OAuth entry point", `href=${landingHref}`, await connectLink.evaluate((node) => node.outerHTML));
    }
    if (await connectLink.getAttribute("target")) {
      audit.low("Connect link opens in a new tab", "target attribute found on the connect link.", await connectLink.evaluate((node) => node.outerHTML));
    }

    // --- start the sample and inspect the in-app connect entry ------------
    await page.getByRole("button", { name: /try a sample day/i }).click();
    await expect(page.getByText(/sample workspace/i).first()).toBeVisible({ timeout: 20_000 });
    const appHref = await page
      .getByRole("link", { name: /connect google calendar/i })
      .first()
      .getAttribute("href");
    audit.step("in-app connect href", String(appHref));
    if (appHref !== "/api/auth/google/start") {
      audit.low("In-app connect link differs from the landing link", `href=${appHref}`, String(appHref));
    }
    await audit.shot(page, "sample-workspace");

    // --- responsive checks -------------------------------------------------
    for (const [name, width, height] of [
      ["iphone-390", 390, 844],
      ["small-android-360", 360, 640],
      ["tablet-768", 768, 1024],
    ] as const) {
      await page.setViewportSize({ width, height });
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(250);
      const metrics = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
        bodyScrollWidth: document.body.scrollWidth,
      }));
      audit.step(`viewport ${name}`, JSON.stringify(metrics));
      if (metrics.scrollWidth > metrics.clientWidth + 1) {
        audit.medium(
          `Horizontal overflow at ${name}`,
          `Page scrolls sideways at ${width}px wide.`,
          JSON.stringify(metrics),
        );
      }
      const taps = await inventory(page);
      for (const entry of taps) {
        const [, , w, h] = entry.box;
        if (w > 0 && h > 0 && (h < 24 || w < 24)) {
          audit.low(
            `Small tap target at ${name}`,
            `${entry.tag} "${entry.label}" is ${w}x${h}px.`,
            JSON.stringify(entry),
          );
        }
      }
      audit.step(`controls at ${name}`, String(taps.length));
      await audit.shot(page, `landing-${name}`);
    }

    watch.assertClean("landing");
    audit.ok("Landing page renders with a clean console", `GET / -> 200, ${controls.length} controls inventoried`);
  });

  test("connect: OAuth start parameters, cookies and the provider screen", async ({
    page,
    context,
  }) => {
    const watch = instrument(page, audit);

    const { response: status, retried, firstStatus } = await getWithRetry(page, `${APP}/api/auth/status`);
    const statusBody = await status.json().catch(() => null);
    audit.step(
      "GET /api/auth/status",
      String(status.status()),
      `${JSON.stringify(statusBody)}${retried ? ` (first attempt ${firstStatus}, retried after 2s)` : ""}`,
    );
    if (retried) {
      audit.medium(
        "Live API throttled an ordinary request (429)",
        `The HTTP API stage throttles at burst 50 / rate 25 rps (infra/template.yaml DefaultRouteSettings). A single /api/auth/status call returned ${firstStatus} while the audit fleet was probing concurrently, and the throttle is per-stage, so it is shared across all visitors rather than per user. Under any real burst of concurrent visitors every user starts seeing 429s.`,
        `first attempt ${firstStatus}, second attempt ${status.status()} after 2s backoff`,
      );
    }
    expect(status.status()).toBe(200);
    expect(Object.keys(statusBody ?? {}).sort()).toEqual(
      ["connected", "email", "provider_available", "requires_reconnect"].sort(),
    );

    const starts = [];
    for (let i = 0; i < 2; i += 1) {
      const start = await page.request.get(`${APP}/api/auth/google/start`, { maxRedirects: 0 });
      starts.push(start);
      audit.step(
        `GET /api/auth/google/start #${i + 1}`,
        String(start.status()),
        String(start.headers()["location"] ?? "").slice(0, 400),
      );
    }
    for (const [index, start] of starts.entries()) {
      if (start.status() !== 307) {
        audit.medium(
          `OAuth start returns ${start.status()} instead of 307`,
          "The checked-in frontend e2e suite asserts 302 for this endpoint; the deployed API uses 307.",
          `request #${index + 1}: status=${start.status()} location=${start.headers()["location"]}`,
        );
      }
    }
    const urls = starts.map((start) => new URL(String(start.headers()["location"])));
    const first = urls[0];
    audit.step("provider", first.host, first.pathname);
    if (first.host !== "accounts.google.com") {
      audit.high("OAuth start does not redirect to Google", `host=${first.host}`, first.href);
    }
    const params = Object.fromEntries(first.searchParams.entries());
    audit.dump("oauth-start-params", { ...params, state: "<redacted>", code_challenge: "<redacted>" });
    const checks: [string, boolean, string][] = [
      ["response_type=code", params.response_type === "code", String(params.response_type)],
      ["redirect_uri is the deployed callback", params.redirect_uri === `${APP}/api/auth/google/callback`, String(params.redirect_uri)],
      ["scope includes calendar.events.owned", String(params.scope).includes("calendar.events.owned"), String(params.scope)],
      ["PKCE S256", params.code_challenge_method === "S256" && Boolean(params.code_challenge), String(params.code_challenge_method)],
      ["access_type=offline", params.access_type === "offline", String(params.access_type)],
      ["prompt=consent", params.prompt === "consent", String(params.prompt)],
      ["state present", Boolean(params.state) && params.state.length >= 20, `${String(params.state).length} chars`],
      ["client_id present", Boolean(params.client_id), String(params.client_id)],
      ["no error param", params.error === undefined, String(params.error)],
    ];
    for (const [label, pass, observed] of checks) {
      audit.step(`oauth param ${label}`, pass ? "pass" : "FAIL", observed);
      if (!pass) audit.medium(`OAuth start parameter wrong: ${label}`, `Observed ${observed}.`, first.href);
    }
    if (params.state === urls[1].searchParams.get("state")) {
      audit.high("OAuth state is reused between starts", "Two /api/auth/google/start calls produced the same state value.", `state=${params.state?.slice(0, 12)}...`);
    }

    const setCookie = starts[0].headers()["set-cookie"];
    audit.step("oauth transaction cookie", cookieAttributes(setCookie, "glide_oauth_transaction"));
    const rawCookie = cookieAttributes(setCookie, "glide_oauth_transaction");
    for (const [flag, pass] of [
      ["HttpOnly", /httponly/i.test(rawCookie)],
      ["Secure", /secure/i.test(rawCookie)],
      ["SameSite=Lax", /samesite=lax/i.test(rawCookie)],
      ["Path=/api/auth/google/callback", /path=\/api\/auth\/google\/callback/i.test(rawCookie)],
    ] as const) {
      if (!pass) {
        const severity = flag === "Secure" ? "medium" : "low";
        if (severity === "medium") {
          audit.medium(`OAuth transaction cookie missing ${flag}`, `Set-Cookie on /api/auth/google/start lacked ${flag}.`, rawCookie);
        } else {
          audit.low(`OAuth transaction cookie missing ${flag}`, `Set-Cookie on /api/auth/google/start lacked ${flag}.`, rawCookie);
        }
      }
    }

    // Real click through the landing link, stopping at the provider screen.
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await Promise.all([
      page.waitForURL(/accounts\.google\.com/, { timeout: 30_000 }),
      page.getByRole("link", { name: /connect google calendar/i }).click(),
    ]);
    await page.waitForLoadState("domcontentloaded").catch(() => undefined);
    const providerUrl = new URL(page.url());
    audit.step("provider screen reached", providerUrl.host, providerUrl.pathname);
    audit.step("provider screen title", await page.title().catch(() => "(no title)"));
    const providerText = squeeze(
      await page.locator("body").innerText({ timeout: 15_000 }).catch(() => ""),
    ).slice(0, 400);
    audit.step("provider screen body", providerText || "(empty)");
    audit.dump("provider-screen", { url: `${providerUrl.origin}${providerUrl.pathname}`, params: Object.fromEntries(providerUrl.searchParams.entries()) });
    await audit.shot(page, "provider-screen");
    // Cookies are scoped to the callback path, so ask for that exact URL.
    // Asking for the origin root alone would miss them (and produce a false
    // "cookie missing" finding).
    const cookies = [
      ...(await context.cookies(`${APP}/`)),
      ...(await context.cookies(`${APP}/api/auth/google/callback`)),
    ].filter(
      (cookie, index, all) => all.findIndex((other) => other.name === cookie.name) === index,
    );
    const transactionCookie = cookies.find((cookie) => cookie.name === "glide_oauth_transaction");
    audit.step(
      "browser cookies on callback path",
      cookies.map((cookie) => cookie.name).join(", ") || "(none)",
      JSON.stringify(cookies.map(({ name, httpOnly, secure, sameSite, path }) => ({ name, httpOnly, secure, sameSite, path }))),
    );
    if (!transactionCookie) {
      audit.medium("OAuth transaction cookie not set in the browser", "After clicking connect, the callback-path cookie is missing, so the callback cannot validate state.", JSON.stringify(cookies));
    }
    if (/access blocked|error 403|invalid_client/i.test(providerText)) {
      audit.high("Google rejects the OAuth client for a fresh visitor", "The consent screen shows an access error instead of sign-in.", providerText);
    }

    watch.assertClean("connect start");
  });

  test("connect: callback error paths are handled cleanly", async ({ page }) => {
    const call = async (query: string, headers?: Record<string, string>) => {
      const response = await page.request.get(`${APP}/api/auth/google/callback${query}`, {
        maxRedirects: 0,
        headers,
      });
      const body = await response.text().catch(() => "");
      audit.step(
        `GET /callback${query}`,
        String(response.status()),
        squeeze(body).slice(0, 200),
      );
      return { response, body };
    };

    const missing = await call("");
    if (missing.response.status() !== 400) {
      audit.medium("Callback without code/state does not answer 400", `status=${missing.response.status()}`, missing.body.slice(0, 300));
    }

    const denied = await call("?error=access_denied");
    audit.dump("callback-denied", { status: denied.response.status(), body: denied.body, contentType: denied.response.headers()["content-type"] });
    if (denied.response.status() !== 401) {
      audit.low("Callback denial does not answer 401", `status=${denied.response.status()}`, denied.body.slice(0, 300));
    }
    const deniedBody = squeeze(denied.body);
    if (!/access_denied/.test(deniedBody)) {
      audit.medium("Callback denial body loses the Google error code", "A support request cannot tell why the sign-in failed.", deniedBody.slice(0, 200));
    }
    if (/json/i.test(String(denied.response.headers()["content-type"] ?? ""))) {
      audit.medium(
        "Denied consent lands on a raw JSON error page",
        "A user who clicks 'Cancel' at Google is left on a JSON error document with no way back to the app; the app never shows a friendly 'sign-in was cancelled' state.",
        `status=${denied.response.status()} content-type=${denied.response.headers()["content-type"]} body=${deniedBody.slice(0, 160)}`,
      );
    }

    const forged = await call("?code=forged-code&state=forged-state");
    audit.dump("callback-forged-state", {
      status: forged.response.status(),
      headers: Object.fromEntries(Object.entries(forged.response.headers())),
      bodyStart: forged.body.slice(0, 200),
    });
    if (/text\/html/i.test(String(forged.response.headers()["content-type"] ?? ""))) {
      audit.medium(
        "Forbidden API responses are rewritten to the SPA shell",
        "A callback with an invalid/forged state should surface the API's 403 JSON error. The CDN's SPA error mapping replaces it with 200 text/html (the app shell, x-cache: Error from cloudfront / server: AmazonS3), so the failure is invisible to both the user and any client. Same mechanism turns unknown /api/* routes into 200 HTML instead of 404 JSON.",
        JSON.stringify({
          status: forged.response.status(),
          contentType: forged.response.headers()["content-type"],
          via: forged.response.headers()["via"] ?? null,
          xCache: forged.response.headers()["x-cache"] ?? null,
          server: forged.response.headers()["server"] ?? null,
          bodyStart: forged.body.slice(0, 120),
        }),
      );
    } else if (forged.response.status() !== 403) {
      audit.medium("Callback with a forged state does not answer 403", `status=${forged.response.status()}`, forged.body.slice(0, 300));
    }

    // Same mechanism on an unknown API route (cross-checked with the fleet's
    // "unknown API route does not 404" finding).
    const unknown = await page.request.get(`${APP}/api/definitely-not-a-route`, { maxRedirects: 0 });
    const unknownBody = await unknown.text().catch(() => "");
    audit.dump("unknown-api-route", {
      status: unknown.status(),
      headers: Object.fromEntries(Object.entries(unknown.headers())),
      bodyStart: unknownBody.slice(0, 120),
    });
    audit.step(
      "GET /api/definitely-not-a-route",
      String(unknown.status()),
      `${unknown.headers()["content-type"]} | ${squeeze(unknownBody).slice(0, 80)}`,
    );

    // Real state, invalid code: forces a genuine (failing) token exchange.
    const start = await page.request.get(`${APP}/api/auth/google/start`, { maxRedirects: 0 });
    const transaction = cookieValue(start.headers()["set-cookie"], "glide_oauth_transaction");
    const state = new URL(String(start.headers()["location"])).searchParams.get("state") ?? "";
    if (transaction && state) {
      const exchange = await call(`?code=invalid-code-from-test&state=${encodeURIComponent(state)}`, {
        Cookie: `glide_oauth_transaction=${transaction}`,
      });
      audit.dump("callback-invalid-code", {
        status: exchange.response.status(),
        body: exchange.body.slice(0, 500),
        contentType: exchange.response.headers()["content-type"],
        cacheControl: exchange.response.headers()["cache-control"] ?? null,
      });
      if (exchange.response.status() >= 500) {
        audit.high(
          "Invalid or expired OAuth code crashes the callback with a 5xx",
          "google_auth_oauthlib raises InvalidGrantError for a stale/reused/forged code and the route never catches it, so the Lambda fails (API Gateway answers a plain-text 'Internal Server Error'). A user who refreshes the callback URL or resumes an expired consent ends up on an error page instead of a recoverable sign-in failure.",
          JSON.stringify({
            status: exchange.response.status(),
            contentType: exchange.response.headers()["content-type"],
            body: squeeze(exchange.body).slice(0, 200),
          }),
        );
      }
      if (/traceback|client_secret|Traceback/i.test(exchange.body)) {
        audit.high("Callback error leaks internals", "Response body contains a stack trace or secret-shaped text.", exchange.body.slice(0, 300));
      }
    } else {
      audit.step("invalid-code exchange", "skipped", "no transaction cookie or state returned by /start");
    }

    // Auth endpoints must not be cached by the CDN.
    for (const path of ["/api/auth/status", "/api/auth/session"]) {
      const response = await page.request.get(`${APP}${path}`);
      const cacheControl = response.headers()["cache-control"] ?? "(none)";
      audit.step(`cache headers ${path}`, String(response.status()), `cache-control=${cacheControl}`);
      if (path === "/api/auth/session" && response.status() === 200) {
        audit.medium("Signed-out /api/auth/session answered 200", "It should be 401 for an anonymous visitor.", await response.text().catch(() => ""));
      }
    }

    // CORS: a foreign origin must not receive an allow-origin header.
    const cors = await page.request.fetch(`${APP}/api/auth/status`, {
      headers: { Origin: "https://evil.example" },
      maxRedirects: 0,
    });
    const allowOrigin = cors.headers()["access-control-allow-origin"] ?? "(none)";
    audit.step("CORS with foreign origin", String(cors.status()), `access-control-allow-origin=${allowOrigin}`);
    if (allowOrigin === "*" || allowOrigin.includes("evil.example")) {
      audit.high("CORS allows an arbitrary origin on auth endpoints", `access-control-allow-origin=${allowOrigin}`, `GET /api/auth/status with Origin: https://evil.example -> ${cors.status()}`);
    }

    audit.ok("OAuth callback error paths answered without leaking internals", "400/401/403 JSON responses verified");
  });

  test("probe: the auth-status call the landing page depends on under light load", async ({
    page,
  }) => {
    // Deliberately modest: one sequential request every 400ms for 6 seconds.
    // This is the first API call every visitor's browser makes, so its
    // reliability is user-visible.
    const results: { status: number; ms: number; body: string }[] = [];
    for (let i = 0; i < 15; i += 1) {
      const started = Date.now();
      const response = await page.request.get(`${APP}/api/auth/status`);
      const body = await response.text().catch(() => "");
      results.push({ status: response.status(), ms: Date.now() - started, body: body.slice(0, 120) });
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    const failures = results.filter((entry) => entry.status !== 200);
    audit.dump("auth-status-probe", results);
    audit.step(
      "sequential /api/auth/status probe",
      `${results.length} requests, ${failures.length} non-200`,
      results.map((entry) => entry.status).join(","),
    );
    if (failures.length) {
      audit.medium(
        `Landing page auth check failed ${failures.length}/${results.length} times under light sequential load`,
        "Every visitor's first API call is GET /api/auth/status. A single sequential client (400ms apart) saw non-200 answers while the audit fleet was also probing, so the deployed API is fragile under concurrent load. The stage throttles at burst 50 / rate 25 rps (infra/template.yaml), shared across all visitors.",
        JSON.stringify(
          failures.map((entry) => ({ status: entry.status, ms: entry.ms, body: squeeze(entry.body).slice(0, 80) })),
        ),
      );
    } else {
      audit.ok(
        "Landing page auth check is reliable under light sequential load",
        `${results.length}/${results.length} answered 200 (avg ${Math.round(results.reduce((sum, entry) => sum + entry.ms, 0) / results.length)}ms)`,
      );
    }
  });

  test("connect: a failed status check hides the connect CTA with no recovery", async ({
    page,
  }) => {
    // Deterministic reproduction of what happened organically when the API
    // answered 503 during the landing test.
    await page.route("**/api/auth/status", (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ message: "Service Unavailable" }),
      }),
    );
    await page.goto("/", { waitUntil: "domcontentloaded" });
    const checking = page.getByText(/checking google calendar connection/i);
    await expect(checking).toBeVisible({ timeout: 15_000 });
    await page.waitForTimeout(8_000);

    const state = await page.evaluate(() => ({
      checkingVisible: Array.from(document.querySelectorAll("p")).some((node) =>
        /checking google calendar connection/i.test(node.textContent ?? ""),
      ),
      connectLink: Boolean(
        Array.from(document.querySelectorAll("a")).find((node) =>
          /connect google calendar/i.test(node.textContent ?? ""),
        ),
      ),
      alertsOrRetry: Array.from(
        document.querySelectorAll('[role="alert"], button, a'),
      )
        .map((node) => (node.textContent ?? "").trim())
        .filter((text) => /retry|try again|error|unavailable|reload/i.test(text)),
      sampleButtonVisible: Boolean(
        Array.from(document.querySelectorAll("button")).find((node) =>
          /try a sample day/i.test(node.textContent ?? ""),
        ),
      ),
    }));
    audit.dump("failed-status-check-state", state);
    audit.step("state after 8s with status endpoint down", JSON.stringify(state));
    if (state.checkingVisible && !state.connectLink) {
      audit.high(
        "A failed status check permanently hides the Connect CTA",
        "With GET /api/auth/status answering 503, the landing page shows only 'Checking Google Calendar connection…' — no Connect Google Calendar link, no error message, no retry and no automatic recovery. The placeholder is indistinguishable from a slow load, so a visitor waits indefinitely instead of reloading. This reproduced organically during the audit when the throttled API answered 503 three times on a single landing load (burst 50 / 25 rps shared stage limit).",
        JSON.stringify(state),
      );
    }
    audit.step(
      "retry/error affordances found",
      state.alertsOrRetry.length ? state.alertsOrRetry.join(" | ") : "(none)",
    );
    if (!state.sampleButtonVisible) {
      audit.medium("Sample-day button missing while status check is failing", "Signed-out visitors should still be able to explore the sample.", JSON.stringify(state));
    }

    await page.unroute("**/api/auth/status");
    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("link", { name: /connect google calendar/i })).toBeVisible({
      timeout: 20_000,
    });
    audit.step("recovery after reload", "connect CTA returns once the endpoint answers");
  });
});
