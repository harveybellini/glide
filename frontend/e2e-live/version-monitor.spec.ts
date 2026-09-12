// Live proof for the version monitor: the deployed bundle names the build it is
// running, the host serves that same build's version.json, the API reports the
// same version, and a tab that sees a different deployed commit is offered a
// reload.
import { test } from "@playwright/test";
import { Audit, LIVE_BASE, expect, instrument } from "./harness";

const audit = new Audit("version-monitor");

test.afterAll(() => {
  audit.write();
});

test("the live page reports the build the host is serving", async ({ page }) => {
  const watch = instrument(page, audit);

  const manifestResponse = await page.request.get(`${LIVE_BASE}/version.json`);
  const manifest = (await manifestResponse.json().catch(() => null)) as {
    version: string;
    commit: string;
  } | null;
  audit.step(
    "GET /version.json",
    String(manifestResponse.status()),
    JSON.stringify(manifest),
  );
  expect(manifestResponse.status(), "version.json status").toBe(200);
  expect(String(manifest?.version), "manifest version").toMatch(/^\d+\.\d+\.\d+$/);
  expect(String(manifest?.commit ?? ""), "manifest commit").not.toHaveLength(0);

  const healthResponse = await page.request.get(`${LIVE_BASE}/api/health`);
  const health = (await healthResponse.json().catch(() => null)) as {
    version?: string;
  } | null;
  audit.step(
    "GET /api/health",
    String(healthResponse.status()),
    JSON.stringify(health),
  );
  expect(health?.version, "the API reports the page's version").toBe(
    manifest?.version,
  );

  await page.goto(LIVE_BASE, { waitUntil: "networkidle" });
  const badge = page.getByRole("button", { name: /version details/i });
  await expect(badge).toBeVisible();
  await expect(badge).toContainText(`v${manifest?.version}`);
  await badge.click();

  const panel = page.locator("#version-panel");
  await expect(panel).toContainText("Up to date");
  await expect(panel.locator(".version-row", { hasText: "API" })).toContainText(
    `v${manifest?.version}`,
  );
  await audit.shot(page, "footer-version-panel");
  audit.ok(
    "The live page and API report one version",
    `v${manifest?.version} (${manifest?.commit})`,
  );

  // A newer deployed build must raise the notice on an already-open tab.
  await page.route("**/version.json", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...manifest, commit: "newerbuild" }),
    }),
  );
  await page.getByRole("button", { name: /check now/i }).click();
  const notice = page.getByText(/a new version of glide is ready/i);
  await expect(notice).toBeVisible();
  audit.shot(page, "newer-build-notice");
  audit.ok("An open tab is told when a newer build is live", "commit newerbuild");

  // "Later" silences this build only.
  await page.getByRole("button", { name: "Later" }).click();
  await expect(notice).toHaveCount(0);

  watch.assertClean("version monitor");
});
