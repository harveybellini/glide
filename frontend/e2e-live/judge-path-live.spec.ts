// Coordinator canary: the canonical judge path from frontend/e2e/judge-path.spec.ts,
// replayed against the deployed CloudFront site. Deliberately independent of the
// per-area agents so a failure here is a strong signal.
import { test, expect } from "@playwright/test";
import { Audit, instrument, squeeze } from "./harness";

const audit = new Audit("judge-path-live");
test.afterAll(() => audit.write());

test("judge path on the live deployment", async ({ page }) => {
  const watch = instrument(page, audit);

  await page.goto("/", { waitUntil: "networkidle" });
  await expect(page.getByRole("heading", { name: "Glide" })).toBeVisible();
  audit.ok("landing renders", "Glide heading visible");

  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible({ timeout: 30000 });
  await audit.shot(page, "workspace-fresh");

  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByRole("heading", { name: /needs your decision/i })).toBeVisible({
    timeout: 60000,
  });
  const decisionCopy = squeeze(await page.locator(".decisions").innerText());
  audit.step("first decision copy", "captured", decisionCopy);
  const travelCount = await page.getByText("Travel \u00b7 Glide", { exact: true }).count();
  audit.step("travel blocks after first check", String(travelCount));
  await audit.shot(page, "workspace-first-check");

  // Move the middle appointment through the inline editor.
  await page.getByRole("button", { name: "Edit" }).nth(1).click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible();
  await editor.getByLabel("Start", { exact: true }).fill("10:45");
  await editor.getByLabel("End", { exact: true }).fill("11:15");
  await page.getByRole("button", { name: /save changes/i }).click();
  await expect(page.getByText(/appointment updated/i)).toBeVisible({ timeout: 30000 });

  // Recheck resolves the conflict into two blocks.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible({ timeout: 60000 });
  await expect(page.getByRole("heading", { name: /needs your decision/i })).toHaveCount(0);
  const resolved = await page.getByText("Travel \u00b7 Glide", { exact: true }).count();
  audit.step("travel blocks after resolving", String(resolved));
  await audit.shot(page, "workspace-resolved");

  // A repeat run must not create duplicates.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible({ timeout: 60000 });
  const afterRepeat = await page.getByText("Travel \u00b7 Glide", { exact: true }).count();
  audit.step("travel blocks after repeat", String(afterRepeat));
  if (afterRepeat !== resolved) {
    audit.high(
      "Repeat check duplicates travel blocks",
      `Expected ${resolved} travel blocks after an idempotent repeat run, saw ${afterRepeat}.`,
      `counts: first=${travelCount} resolved=${resolved} repeat=${afterRepeat}`,
    );
  } else {
    audit.ok("repeat check is idempotent", `${afterRepeat} travel blocks unchanged`);
  }

  // Reset returns to the starting state.
  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/no updates yet/i)).toBeVisible({ timeout: 30000 });
  const afterReset = await page.getByText("Travel \u00b7 Glide", { exact: true }).count();
  if (afterReset !== 0) {
    audit.high("Reset leaves travel blocks behind", `Saw ${afterReset} blocks after reset.`);
  } else {
    audit.ok("reset clears the day", "no travel blocks after reset");
  }
  await audit.shot(page, "workspace-after-reset");

  watch.assertClean("judge path");
});
