// Smoke probe: proves the live site answers from this machine and gives the
// audit a baseline for the landing screen.
import { test } from "@playwright/test";
import { Audit, LIVE_BASE, expect, inventory, instrument } from "./harness";

const audit = new Audit("00-smoke");

test.afterAll(() => {
  audit.write();
});

test("landing page renders and the API answers", async ({ page }) => {
  const watch = instrument(page, audit);

  const response = await page.goto(LIVE_BASE, { waitUntil: "networkidle" });
  audit.step("GET /", String(response?.status() ?? "no response"));
  expect(response?.status(), "landing page HTTP status").toBe(200);

  audit.step("title", await page.title());
  await expect(page.getByRole("button", { name: /Try a sample day/i })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Life happens/i })).toBeVisible();

  const controls = await inventory(page);
  audit.dump("landing-inventory", controls);
  audit.step("interactive controls on landing", String(controls.length));
  await audit.shot(page, "landing");

  const authStatus = await page.request.get(`${LIVE_BASE}/api/auth/status`);
  audit.step(
    "GET /api/auth/status",
    String(authStatus.status()),
    JSON.stringify(await authStatus.json().catch(() => null)),
  );

  const cached = await page.request.get(LIVE_BASE);
  audit.step(
    "response headers",
    String(cached.status()),
    JSON.stringify({
      cacheControl: cached.headers()["cache-control"] ?? null,
      via: cached.headers()["x-cache"] ?? cached.headers()["via"] ?? null,
      contentType: cached.headers()["content-type"] ?? null,
    }),
  );

  watch.assertClean("landing");
  audit.ok("Live site reachable", `GET / returned ${response?.status()}`);
});
