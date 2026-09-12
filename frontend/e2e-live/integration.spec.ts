import { expect, test } from "@playwright/test";

// Lead agent: cross-component integration. Each test starts its own sample
// day, so settings/automation state is isolated per test.

async function startSample(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();
}

async function recheck(page: import("@playwright/test").Page, label: string) {
  const started = Date.now();
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated|decision needs your input/i)).toBeVisible({
    timeout: 240_000,
  });
  console.log(`RECHECK_MS ${label}`, Date.now() - started);
}

async function openSettings(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.locator("form.settings-panel")).toBeVisible();
}

async function saveSettings(page: import("@playwright/test").Page) {
  await page.locator("form.settings-panel").getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible({ timeout: 20_000 });
}

test("account bar: sample mode offers a calendar connection link", async ({ page }) => {
  await startSample(page);
  const bar = page.locator(".account-bar");
  await expect(bar).toBeVisible();
  const text = (await bar.innerText()).replace(/\s+/g, " ").trim();
  console.log("ACCOUNT_BAR_TEXT", JSON.stringify(text));

  const link = bar.getByRole("link", { name: /connect google calendar/i });
  const href = await link.getAttribute("href");
  console.log("CONNECT_HREF", href);
  console.log("CONNECT_TARGET", await link.getAttribute("target"));
  await expect(link).toBeVisible();
});

test("arrival buffer propagates into the timeline, stats and decision math", async ({ page }) => {
  test.setTimeout(600_000);
  await startSample(page);
  await recheck(page, "default-10");

  const statsBefore = (await page.locator(".day-stats").innerText()).replace(/\s+/g, " ");
  const travelBefore = (await page.locator("article.travel").first().innerText()).replace(/\s+/g, " ");
  const decisionBefore = (await page.locator(".decision").first().innerText().catch(() => "NONE")).replace(/\s+/g, " ");
  console.log("BEFORE_STATS", JSON.stringify(statsBefore));
  console.log("BEFORE_TRAVEL", JSON.stringify(travelBefore));
  console.log("BEFORE_DECISION", JSON.stringify(decisionBefore));

  await openSettings(page);
  await page.locator("form.settings-panel").getByLabel("Arrival buffer (minutes)").fill("0");
  await saveSettings(page);
  const statsAfterZero = (await page.locator(".day-stats").innerText()).replace(/\s+/g, " ");
  console.log("AFTER_ZERO_STATS", JSON.stringify(statsAfterZero));
  await expect(page.locator(".day-stats")).toContainText("0");

  await recheck(page, "buffer-0");
  const travelZero = (await page.locator("article.travel").first().innerText().catch(() => "NO_TRAVEL")).replace(/\s+/g, " ");
  const decisionZero = (await page.locator(".decision").first().innerText().catch(() => "NONE")).replace(/\s+/g, " ");
  console.log("BUFFER_0_TRAVEL", JSON.stringify(travelZero));
  console.log("BUFFER_0_DECISION", JSON.stringify(decisionZero));

  await openSettings(page);
  await page.locator("form.settings-panel").getByLabel("Arrival buffer (minutes)").fill("25");
  await saveSettings(page);
  await recheck(page, "buffer-25");
  const travel25 = (await page.locator("article.travel").first().innerText().catch(() => "NO_TRAVEL")).replace(/\s+/g, " ");
  const decision25 = (await page.locator(".decision").first().innerText().catch(() => "NONE")).replace(/\s+/g, " ");
  const stats25 = (await page.locator(".day-stats").innerText()).replace(/\s+/g, " ");
  console.log("BUFFER_25_TRAVEL", JSON.stringify(travel25));
  console.log("BUFFER_25_DECISION", JSON.stringify(decision25));
  console.log("BUFFER_25_STATS", JSON.stringify(stats25));
});

test("time zone setting vs the hardcoded 'Times in London' label", async ({ page }) => {
  test.setTimeout(300_000);
  await startSample(page);
  const heading = (await page.locator(".section-heading").innerText()).replace(/\s+/g, " ");
  console.log("SECTION_HEADING_DEFAULT", JSON.stringify(heading));
  const timesBefore = await page.locator("#timeline article .time").allInnerTexts();
  console.log("TIMES_BEFORE", JSON.stringify(timesBefore));

  await openSettings(page);
  await page.locator("form.settings-panel").getByLabel("Time zone").selectOption("America/New_York");
  await saveSettings(page);
  await page.reload();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible({ timeout: 30_000 });

  const headingAfter = (await page.locator(".section-heading").innerText()).replace(/\s+/g, " ");
  const timesAfter = await page.locator("#timeline article .time").allInnerTexts();
  console.log("SECTION_HEADING_AFTER_TZ", JSON.stringify(headingAfter));
  console.log("TIMES_AFTER_TZ", JSON.stringify(timesAfter));
});

test("start address setting propagates to the journey note and travel origin", async ({ page }) => {
  test.setTimeout(300_000);
  await startSample(page);
  const noteBefore = (await page.locator(".journey-note").innerText()).replace(/\s+/g, " ");
  console.log("NOTE_BEFORE", JSON.stringify(noteBefore));

  await openSettings(page);
  await page.locator("form.settings-panel").getByLabel("Start address").selectOption("place_c");
  await saveSettings(page);
  await recheck(page, "place-c");

  const noteAfter = (await page.locator(".journey-note").innerText()).replace(/\s+/g, " ");
  const travelAfter = (await page.locator("article.travel").first().innerText().catch(() => "NO_TRAVEL")).replace(/\s+/g, " ");
  console.log("NOTE_AFTER_PLACE_C", JSON.stringify(noteAfter));
  console.log("TRAVEL_AFTER_PLACE_C", JSON.stringify(travelAfter));
});

test("reset sample: does it also restore settings?", async ({ page }) => {
  test.setTimeout(300_000);
  await startSample(page);
  await openSettings(page);
  await page.locator("form.settings-panel").getByLabel("Arrival buffer (minutes)").fill("33");
  await page.locator("form.settings-panel").getByLabel("Start address").selectOption("place_b");
  await saveSettings(page);
  console.log("SET_BEFORE_RESET", JSON.stringify((await page.locator(".day-stats").innerText()).replace(/\s+/g, " ")));

  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/sample reset to its starting state/i)).toBeVisible({ timeout: 30_000 });
  const statsAfterReset = (await page.locator(".day-stats").innerText()).replace(/\s+/g, " ");
  console.log("STATS_AFTER_RESET", JSON.stringify(statsAfterReset));

  await openSettings(page);
  const paddingValue = await page.locator("form.settings-panel").getByLabel("Arrival buffer (minutes)").inputValue();
  const placeValue = await page.locator("form.settings-panel").getByLabel("Start address").inputValue();
  console.log("SETTINGS_AFTER_RESET", JSON.stringify({ paddingValue, placeValue }));
});

test("paused automation vs Recheck now", async ({ page }) => {
  test.setTimeout(300_000);
  await startSample(page);
  await page.getByRole("button", { name: /pause automation/i }).click();
  await expect(page.getByText(/automation paused/i)).toBeVisible({ timeout: 20_000 });
  const sidebar = (await page.locator(".sidebar-bottom").innerText()).replace(/\s+/g, " ");
  console.log("SIDEBAR_PAUSED", JSON.stringify(sidebar));

  await page.getByRole("button", { name: /recheck now/i }).click();
  await page.waitForTimeout(30_000);
  const status = (await page.locator("p.status").innerText()).replace(/\s+/g, " ");
  const travelCount = await page.locator("article.travel").count();
  const decisions = await page.locator(".decision").count();
  console.log("PAUSED_RECHECK", JSON.stringify({ status, travelCount, decisions }));
});
