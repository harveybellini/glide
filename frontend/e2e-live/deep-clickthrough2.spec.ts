// Exhaustive control-by-control click-through of the deployed site, v2.
//
// Fixes the two artefacts of v1: state is rebuilt deterministically through
// the API before every single click (so clicking "Pause"/"Reset" cannot leak
// into the next control), and the Google OAuth link is verified without
// following the redirect (so no third-party page pollutes the run).
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

const audit = new Audit("deep-clickthrough-v2");
test.describe.configure({ mode: "serial" });
test.setTimeout(1_200_000);

test.afterAll(() => {
  audit.write();
});

type Inventory = Awaited<ReturnType<typeof inventory>>;

async function api(page, method: string, path: string, sessionId: string, body?: unknown) {
  const response = await page.request.fetch(new URL(path, LIVE_BASE).toString(), {
    method,
    headers: { "Content-Type": "application/json", "X-Glide-Session": sessionId },
    data: body === undefined ? undefined : body,
  });
  return { status: response.status(), body: await response.json().catch(() => null) };
}

// Puts the session back into the documented post-recheck state without UI
// timing races: resume, reset, run, poll to a terminal status, reload.
async function rebuild(page, sessionId: string, label: string) {
  await api(page, "POST", "/api/resume", sessionId, {});
  await api(page, "POST", "/api/demo/reset", sessionId, {});
  const queued = await api(page, "POST", "/api/runs", sessionId, { trigger: "sample" });
  const runId = queued.body?.run?.run_id ?? queued.body?.run_id;
  if (!runId) throw new Error(`no run id from /api/runs: ${JSON.stringify(queued.body).slice(0, 200)}`);
  let terminal = null;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    await page.waitForTimeout(1_000);
    const detail = await api(page, "GET", `/api/runs/${runId}`, sessionId);
    const status = detail.body?.run?.status ?? detail.body?.status;
    if (status && !["queued", "running", "pending"].includes(status)) {
      terminal = detail.body;
      break;
    }
  }
  if (!terminal) audit.major(`Run ${runId} never left a non-terminal state`, `Rebuild for "${label}" timed out after 80s.`);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("article.row", { timeout: 30_000 });
  await page.waitForTimeout(500);
  return terminal;
}

function describeControl(entry: Inventory[number]) {
  return `${entry.tag}:${entry.label.replace(/\s+/g, " ").trim().slice(0, 40)}`;
}

type ClickResult = {
  control: string;
  index: number;
  outcome: "state changed" | "no change" | "click failed" | "navigated away" | "absent";
  changes: string[];
  status: string;
  alert: string;
  travelBlocks: string;
  decisions: string;
  error?: string;
};

async function dayStats(page) {
  return page.evaluate(() => {
    const values = Array.from(document.querySelectorAll(".day-stats .stat-value")).map(
      (node) => node.textContent?.trim() ?? "",
    );
    return { appointments: values[0] ?? "", travelBlocks: values[1] ?? "", buffer: values[2] ?? "", decisions: values[3] ?? "" };
  });
}

async function clickOnce(page, entry: Inventory[number]): Promise<ClickResult> {
  const before = await page.evaluate(() => ({
    url: location.href,
    text: document.body.innerText.replace(/\s+/g, " "),
    controls: document.querySelectorAll('a,button,input,select,textarea,[role="button"]').length,
  }));
  let error: string | undefined;
  try {
    await page
      .locator('a,button,input,select,textarea,[role="button"],[tabindex]:not([tabindex="-1"])')
      .nth(entry.index)
      .click({ timeout: 6_000, noWaitAfter: true });
  } catch (caught) {
    error = String(caught).split("\n")[0];
  }
  await page.waitForTimeout(1_500);
  await page.waitForLoadState("networkidle", { timeout: 20_000 }).catch(() => {});
  const after = await page.evaluate(() => ({
    url: location.href,
    text: document.body.innerText.replace(/\s+/g, " "),
    controls: document.querySelectorAll('a,button,input,select,textarea,[role="button"]').length,
  }));
  const changes: string[] = [];
  if (before.url !== after.url) changes.push(`url -> ${after.url.slice(0, 80)}`);
  if (before.controls !== after.controls) changes.push(`controls ${before.controls} -> ${after.controls}`);
  if (before.text !== after.text) {
    const at = [...before.text].findIndex((char, index) => char !== after.text[index]);
    changes.push(
      `text: "...${before.text.slice(Math.max(0, at - 30), at + 30)}" -> "...${after.text.slice(Math.max(0, at - 30), at + 30)}"`,
    );
  }
  const external = !after.url.startsWith(LIVE_BASE);
  const stats = await dayStats(page).catch(() => null);
  return {
    control: describeControl(entry),
    index: entry.index,
    outcome: external
      ? "navigated away"
      : changes.length
        ? "state changed"
        : error
          ? "click failed"
          : "no change",
    changes: changes.slice(0, 3),
    status: (await page.locator("p.status").innerText().catch(() => "")).replace(/\s+/g, " ").trim(),
    alert: (await page.locator('[role="alert"]').innerText().catch(() => "")).replace(/\s+/g, " ").trim(),
    travelBlocks: stats?.travelBlocks ?? "",
    decisions: stats?.decisions ?? "",
    ...(error ? { error } : {}),
  };
}

// --------------------------------------------------------------- landing ----

test("landing: every control responds, none is dead", async ({ page }) => {
  const watch = instrument(page, audit);
  await page.goto("/", { waitUntil: "networkidle" });
  const controls = await inventory(page);
  audit.dump("landing-controls", controls);
  audit.step("landing controls", String(controls.length), controls.map(describeControl).join(", "));

  const results: ClickResult[] = [];
  for (const entry of controls) {
    if (entry.href === "/api/auth/google/start") {
      const start = await page.request.get(new URL("/api/auth/google/start", LIVE_BASE).toString(), {
        maxRedirects: 0,
      });
      const location = start.headers()["location"] ?? "";
      audit.step("GET /api/auth/google/start", String(start.status()), location.slice(0, 120));
      if (![301, 302, 303, 307, 308].includes(start.status())) {
        audit.major("OAuth entry point does not redirect", `Expected 3xx, got ${start.status()}.`);
      }
      if (!/accounts\.google\.com/.test(location)) {
        audit.major("OAuth redirect target is not Google", `Location: ${location.slice(0, 200)}`);
      }
      results.push({ control: "a:Connect Google Calendar", index: entry.index, outcome: "navigated away", changes: [`3xx -> ${location.slice(0, 60)}`], status: "", alert: "", travelBlocks: "", decisions: "" });
      continue;
    }
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("main.landing", { timeout: 20_000 });
    const fresh = (await inventory(page)).find((candidate) => candidate.index === entry.index);
    if (!fresh || fresh.label !== entry.label) {
      results.push({ control: describeControl(entry), index: entry.index, outcome: "absent", changes: [], status: "", alert: "", travelBlocks: "", decisions: "" });
      continue;
    }
    const result = await clickOnce(page, fresh);
    results.push(result);
    audit.step(`click ${result.control}`, result.outcome, result.changes.join(" | "));
    if (result.outcome === "no change") {
      audit.minor(`Dead control on landing: "${entry.label}"`, "Clicking produced no URL, DOM, control-count or text change and no error.", JSON.stringify(result));
    }
  }
  audit.dump("landing-click-results", results);
  watch.assertClean("landing");
  audit.ok("Landing controls exercised", `${results.filter((r) => r.outcome === "state changed").length}/${results.length} changed state`);
});

// -------------------------------------------------------------- day view ----

test("day view: every control responds in the post-recheck state", async ({ page }) => {
  const watch = instrument(page, audit);
  const sessionId = await createSession();
  audit.step("session", "created", sessionId);

  await page.addInitScript(([key, value]: [string, string]) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* opaque origin during a third-party navigation */
    }
  }, [SESSION_KEY, sessionId] as [string, string]);

  const terminal = await rebuild(page, sessionId, "baseline");
  audit.step("baseline run", JSON.stringify(terminal)?.slice(0, 400) ?? "none");
  const stats = await dayStats(page);
  audit.step("baseline day stats", JSON.stringify(stats));
  if (stats.travelBlocks !== "01" || stats.decisions !== "01") {
    audit.major(
      "Fresh sample day does not match the documented judge state",
      `submission/testing-instructions.md promises one travel block and one decision; the live day reports travel blocks=${stats.travelBlocks}, decisions=${stats.decisions}.`,
      JSON.stringify(stats),
    );
  } else {
    audit.ok("Judge path state reproduced", `travel blocks=${stats.travelBlocks}, decisions=${stats.decisions}`);
  }
  await audit.shot(page, "baseline-day");

  const canonical = await inventory(page);
  audit.dump("day-controls", canonical);
  audit.step("day controls", String(canonical.length), canonical.map(describeControl).join(", "));

  const results: ClickResult[] = [];
  for (let index = 0; index < canonical.length; index += 1) {
    const entry = canonical[index];
    if (entry.href === "/api/auth/google/start") {
      audit.step(`click ${describeControl(entry)}`, "skipped", "external OAuth entry point verified separately");
      continue;
    }
    await rebuild(page, sessionId, describeControl(entry));
    const fresh = await inventory(page);
    const current = fresh.find((candidate) => candidate.index === entry.index && candidate.label === entry.label);
    if (!current) {
      const sameLabel = fresh.filter((candidate) => candidate.label === entry.label);
      const fallback = sameLabel[0];
      if (!fallback) {
        results.push({ control: describeControl(entry), index, outcome: "absent", changes: [], status: "", alert: "", travelBlocks: "", decisions: "" });
        audit.step(`click ${describeControl(entry)}`, "absent after rebuild");
        continue;
      }
      results.push(await clickOnce(page, fallback));
      continue;
    }
    const result = await clickOnce(page, current);
    results.push(result);
    audit.step(
      `click ${result.control}`,
      result.outcome,
      [result.changes.join(" | "), result.status, result.alert].filter(Boolean).join(" :: "),
    );
    if (result.outcome === "no change") {
      audit.major(`Dead control in day view: "${entry.label}"`, "Clicking it produced no observable change of any kind.", JSON.stringify(result));
    }
    if (result.error && !/intercepts pointer events/i.test(result.error)) {
      audit.minor(`Click failed in day view: "${entry.label}"`, result.error);
    }
  }
  audit.dump("day-click-results", results);

  // The decision card must be reachable by keyboard, not just by mouse.
  await rebuild(page, sessionId, "keyboard actions");
  const focusOrder: string[] = [];
  for (let press = 0; press < 40; press += 1) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const active = document.activeElement as HTMLElement | null;
      if (!active || active === document.body) return "";
      return `${active.tagName.toLowerCase()}:${(active.getAttribute("aria-label") ?? active.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 40)}`;
    });
    if (!stop) break;
    focusOrder.push(stop);
  }
  audit.dump("keyboard-focus-order", focusOrder);
  audit.step("keyboard focus order", String(focusOrder.length), focusOrder.join(" > "));

  watch.assertClean("day view");
  audit.ok("Day controls exercised", `${results.filter((r) => r.outcome === "state changed").length}/${results.length} changed state`);
  expect(results.length).toBeGreaterThan(0);
});

// ----------------------------------------------------- busy-state / races ---

test("recheck busy state blocks a second submission", async ({ page }) => {
  const sessionId = await createSession();
  await page.addInitScript(([key, value]: [string, string]) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  }, [SESSION_KEY, sessionId] as [string, string]);
  await rebuild(page, sessionId, "busy state");

  await api(page, "POST", "/api/demo/reset", sessionId, {});
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForSelector("button:has-text('Recheck now')", { timeout: 30_000 });

  const button = page.getByRole("button", { name: /recheck now/i });
  await button.click({ noWaitAfter: true });
  await page.waitForTimeout(400);
  const busyState = await page.evaluate(() => {
    const button = document.querySelector("button.primary") as HTMLButtonElement | null;
    return { disabled: button?.disabled ?? null, text: button?.textContent?.replace(/\s+/g, " ").trim() ?? "" };
  });
  audit.step("busy state after click", JSON.stringify(busyState));
  if (!busyState.disabled) {
    audit.minor("Recheck button is not disabled while a run is in flight", `Observed ${JSON.stringify(busyState)} 400ms after the click.`);
  }
  await page.waitForTimeout(3_000);
  const stillBusy = await page.evaluate(() => {
    const button = document.querySelector("button.primary") as HTMLButtonElement | null;
    return { disabled: button?.disabled ?? null, text: button?.textContent?.replace(/\s+/g, " ").trim() ?? "" };
  });
  audit.step("busy state after 3.4s", JSON.stringify(stillBusy));
  await expect(page.locator("p.status")).not.toBeEmpty({ timeout: 90_000 });
  const stats = await dayStats(page);
  audit.step("day stats after single click", JSON.stringify(stats));
  if (Number(stats.decisions) > 1) {
    audit.major("A single Recheck produced more than one decision", JSON.stringify(stats));
  }
});

// ------------------------------------------------------------- resilience ---

test("resilience: a failed request surfaces human copy and recovers", async ({ page }) => {
  const sessionId = await createSession();
  await page.addInitScript(([key, value]: [string, string]) => {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      /* ignore */
    }
  }, [SESSION_KEY, sessionId] as [string, string]);
  await rebuild(page, sessionId, "resilience");

  await page.route("**/api/runs", (route) => route.abort("failed"));
  await page.getByRole("button", { name: /recheck now/i }).click({ noWaitAfter: true });
  await page.waitForTimeout(3_000);
  const failed = {
    status: (await page.locator("p.status").innerText().catch(() => "")).trim(),
    alert: (await page.locator('[role="alert"]').innerText().catch(() => "")).trim(),
    disabled: await page.evaluate(() => (document.querySelector("button.primary") as HTMLButtonElement | null)?.disabled ?? null),
    body: (await page.locator("body").innerText()).replace(/\s+/g, " "),
  };
  audit.dump("failed-request-state", failed);
  audit.step("after aborted run request", `status="${failed.status}" alert="${failed.alert}" disabled=${failed.disabled}`);
  if (/failed to fetch|networkerror|load failed/i.test(failed.body)) {
    audit.minor(
      "Raw browser network error is shown to the user",
      'The page displays the literal fetch error ("Failed to fetch") instead of human copy such as "We could not reach Glide. Try again."',
      failed.status || failed.alert,
    );
  }
  if (/planning travel/i.test(failed.status) && failed.disabled === false) {
    audit.minor("Status line still says a run is in progress after it failed", `status="${failed.status}"`, failed.body.slice(0, 200));
  }
  if (failed.disabled) {
    audit.major("UI stays busy after a failed run", "The Recheck button remains disabled, so the user cannot retry.");
  }

  await page.unroute("**/api/runs");
  await page.getByRole("button", { name: /recheck now/i }).click({ noWaitAfter: true }).catch(() => {});
  await page.waitForTimeout(2_000);
  const recovered = (await page.locator("p.status").innerText().catch(() => "")).trim();
  audit.step("after retry", recovered);
  if (!recovered) audit.medium("No status after retrying once the network is healthy", "The status line stayed empty.");
  await page.context().setOffline(true);
  await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
  await page.waitForTimeout(2_000);
  const offlineBody = (await page.locator("body").innerText().catch(() => "")).replace(/\s+/g, " ");
  audit.step("offline reload body", offlineBody.slice(0, 300));
  await page.context().setOffline(false);
});
