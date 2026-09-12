import { expect, test } from "@playwright/test";

const BASE = "https://d3tvxy281s2u11.cloudfront.net";

test("root journey: landing -> sample -> settings -> editor -> decisions -> pause -> reset", async ({
  page,
}, testInfo) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const failed: string[] = [];
  const api: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("requestfailed", (request) =>
    failed.push(`${request.method()} ${request.url()} :: ${request.failure()?.errorText}`),
  );
  page.on("response", (response) => {
    if (response.url().includes("/api/")) {
      api.push(`${response.request().method()} ${new URL(response.url()).pathname} -> ${response.status()}`);
    }
  });
  const step = async (name: string) => {
    await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
  };

  // 1. Landing
  const landingResponse = await page.goto(BASE, { waitUntil: "domcontentloaded" });
  console.log("LANDING_STATUS", landingResponse?.status());
  await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
  console.log("LANDING_TITLE", await page.title());
  const connect = page.getByRole("link", { name: /connect google calendar/i });
  console.log("CONNECT_LINK_COUNT", await connect.count());
  if (await connect.count()) {
    const href = await connect.first().getAttribute("href");
    console.log("CONNECT_HREF", href);
  }
  console.log("LANDING_BODY", (await page.locator("body").innerText()).replace(/\s+/g, " ").slice(0, 900));
  await step("01-landing");

  // 2. Start sample
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample workspace/i).first()).toBeVisible({ timeout: 15000 });
  console.log("WORKSPACE_BODY_1", (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 1400));
  console.log("EVENT_EDIT_BUTTONS", await page.getByRole("button", { name: "Edit", exact: true }).count());
  await step("02-workspace-fresh");

  // 3. Recheck now
  const recheckStart = Date.now();
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated|decision needs your input/i)).toBeVisible({
    timeout: 120000,
  });
  console.log("RECHECK_MS", Date.now() - recheckStart);
  await expect(page.locator("p.status")).toContainText(/./);
  console.log("STATUS_TEXT", await page.locator("p.status").innerText());
  console.log("TRAVEL_BLOCKS", await page.getByText("Travel · Glide", { exact: true }).count());
  console.log("DECISIONS_BODY", (await page.locator('section[aria-label="Needs your decision"]').innerText().catch(() => "none")).replace(/\s+/g, " "));
  console.log("ACTIVITY", (await page.locator("#activity").innerText()).replace(/\s+/g, " ").slice(0, 500));
  await step("03-after-recheck");

  // 4. Settings: open, verify aria, change buffer, save
  const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
  await settingsButton.click();
  const panel = page.locator("form.settings-panel");
  await expect(panel).toBeVisible();
  console.log("SETTINGS_ARIA_EXPANDED", await settingsButton.getAttribute("aria-expanded"));
  console.log("SETTINGS_ARIA_CONTROLS", await settingsButton.getAttribute("aria-controls"));
  await step("04-settings-open");
  await panel.getByLabel("Arrival buffer (minutes)").fill("15");
  await panel.getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible();
  console.log("BUFFER_STAT", await page.locator(".day-stats").innerText());
  console.log("SETTINGS_CLOSED_AFTER_SAVE", await panel.count());
  console.log("FOCUS_AFTER_SAVE", await page.evaluate(() => document.activeElement?.textContent?.trim()));

  // 5. Event editor on middle appointment
  await page.getByRole("button", { name: "Edit", exact: true }).nth(1).click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible();
  console.log("EDITOR_BODY", (await editor.innerText()).replace(/\s+/g, " "));
  await step("05-editor-open");
  await editor.getByLabel("Start", { exact: true }).fill("");
  await editor.getByRole("button", { name: /save changes/i }).click();
  console.log("EDITOR_EMPTY_START_BODY", (await page.locator("main").innerText()).replace(/\s+/g, " ").slice(0, 400));
  await editor.getByLabel("Start", { exact: true }).fill("10:45");
  await editor.getByLabel("End", { exact: true }).fill("11:15");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await expect(page.getByText(/appointment updated/i)).toBeVisible();
  console.log("TIMELINE_AFTER_EDIT", (await page.locator("#timeline").innerText()).replace(/\s+/g, " "));
  await step("06-after-edit");

  // 6. Recheck resolves the conflict into two blocks
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible({ timeout: 120000 });
  console.log("TRAVEL_BLOCKS_AFTER_FIX", await page.getByText("Travel · Glide", { exact: true }).count());
  console.log("DECISIONS_AFTER_FIX", await page.locator('section[aria-label="Needs your decision"]').count());
  await step("07-after-fix");

  // 7. Pause automation, reload, resume
  await page.getByRole("button", { name: /pause automation/i }).click();
  await expect(page.getByText(/automation paused/i)).toBeVisible();
  console.log("SIDEBAR_AFTER_PAUSE", (await page.locator(".sidebar-bottom").innerText()).replace(/\s+/g, " "));
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByText(/glide is paused/i)).toBeVisible({ timeout: 15000 });
  console.log("PAUSE_PERSISTED_AFTER_RELOAD", true);
  await page.getByRole("button", { name: /resume automation/i }).click();
  await expect(page.getByText(/automation resumed/i)).toBeVisible();
  console.log("SIDEBAR_AFTER_RESUME", (await page.locator(".sidebar-bottom").innerText()).replace(/\s+/g, " "));
  await step("08-after-resume");

  // 8. Reset sample
  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/sample reset to its starting state/i)).toBeVisible();
  await expect(page.getByText(/no updates yet/i)).toBeVisible();
  console.log("TRAVEL_BLOCKS_AFTER_RESET", await page.getByText("Travel · Glide", { exact: true }).count());
  console.log("STATS_AFTER_RESET", await page.locator(".day-stats").innerText());
  await step("09-after-reset");

  console.log("API_CALLS", JSON.stringify(api));
  console.log("CONSOLE_ERRORS", JSON.stringify(consoleErrors));
  console.log("PAGE_ERRORS", JSON.stringify(pageErrors));
  console.log("FAILED_REQUESTS", JSON.stringify(failed));
});
