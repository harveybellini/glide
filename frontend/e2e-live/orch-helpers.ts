// Orchestrator-side helpers for the live audit. Self-contained so that no
// shared harness file has to change; every auditor that needs throttle-aware
// setup can import from here.
import { expect, type APIRequestContext, type Page } from "@playwright/test";
import { LIVE_BASE, SESSION_KEY } from "./harness";

export const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// The deployed API throttles bursts with 429 (no Retry-After, CORS headers
// dropped), and the audit fleet shares one quota, so back off politely.
export async function createSampleSession(request: APIRequestContext, attempts = 8): Promise<string> {
  let lastStatus = 0;
  let lastBody = "";
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await request.post("/api/demo/session", { data: {} });
    lastStatus = response.status();
    if (response.status() === 201) {
      const payload = (await response.json()) as { session: { session_id: string } };
      return payload.session.session_id;
    }
    lastBody = (await response.text()).slice(0, 200);
    if (response.status() === 429) {
      await sleep(2_500 * attempt);
      continue;
    }
    throw new Error(`POST /api/demo/session -> ${response.status()}: ${lastBody}`);
  }
  throw new Error(`POST /api/demo/session stayed throttled (${lastStatus}): ${lastBody}`);
}

// POST /api/runs is throttled at the same shared quota.
export async function queueRun(
  request: APIRequestContext,
  sessionId: string,
  trigger = "sample",
  attempts = 6,
): Promise<{ status: number; runId: string | null; body: string }> {
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const response = await request.post("/api/runs", {
      data: { trigger },
      headers: { "X-Glide-Session": sessionId },
    });
    const body = await response.text();
    if (response.status() === 202) {
      const payload = JSON.parse(body) as { run_id: string };
      return { status: 202, runId: payload.run_id, body };
    }
    if (response.status() === 429 && attempt < attempts) {
      await sleep(2_500 * attempt);
      continue;
    }
    return { status: response.status(), runId: null, body: body.slice(0, 200) };
  }
  throw new Error("queueRun exhausted its attempts");
}

export async function openSample(page: Page, sessionId: string): Promise<void> {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, sessionId],
  );
  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible({ timeout: 20_000 });
}

export interface DayView {
  date: string;
  label: string;
  source_events: { occurrence_id: string; title: string; start: string; end: string; location: string | null }[];
  travel_blocks: {
    journey_key: string;
    start: string;
    end: string;
    padding_minutes?: number;
    origin_occurrence_id: string;
    destination_occurrence_id: string;
  }[];
  decisions: {
    id: string;
    reason: string;
    occurrence_id: string;
    journey_key: string;
    allowed_actions: string[];
    calculated_facts: Record<string, unknown>;
  }[];
  last_run: { status: string; safe_failure_code?: string | null } | null;
}

export async function fetchDay(request: APIRequestContext, sessionId: string): Promise<DayView> {
  const response = await request.get("/api/day", { headers: { "X-Glide-Session": sessionId } });
  expect(response.status(), "GET /api/day").toBe(200);
  return (await response.json()) as DayView;
}

// Clicks Recheck now and waits for the run to finish surfacing in the UI.
export async function recheck(page: Page): Promise<void> {
  const button = page.getByRole("button", { name: /Recheck now/i });
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();
  await expect(button).toBeEnabled({ timeout: 120_000 });
}

export function minutesOfDay(iso: string): number {
  // The sample day is Europe/London in September = UTC+1.
  const date = new Date(iso);
  const london = new Date(date.getTime() + 60 * 60 * 1000);
  return london.getUTCHours() * 60 + london.getUTCMinutes();
}

export { LIVE_BASE };
