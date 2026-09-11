import { expect, test } from "@playwright/test";

async function expectNoHorizontalOverflow(page: import("@playwright/test").Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

test("welcome and daily controls work on narrow screens", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
    await expectNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../submission/screenshots/05-mobile-welcome.png", fullPage: true });
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();
  await page.getByRole("button", { name: "Pause automation" }).click();
  await expect(page.getByText("Glide is paused")).toBeVisible();
  await page.getByRole("button", { name: "Resume automation" }).click();
  await expect(page.getByText("Glide is on")).toBeVisible();
  await page.getByRole("button", { name: "Recheck now" }).click();
  await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    await expectNoHorizontalOverflow(page);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../submission/screenshots/06-mobile-day.png", fullPage: true });
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("Arrival buffer (minutes)")).toBeFocused();
  await expectNoHorizontalOverflow(page);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("form", { name: "Travel settings" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeFocused();
  const edit = page.getByRole("button", { name: "Edit", exact: true }).first();
  await edit.click();
  await expect(page.locator("form.event-editor").getByLabel("Start", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(edit).toBeFocused();
  expect(errors).toEqual([]);
});

test("sample creation errors explain the problem and allow retry", async ({ page }) => {
  await page.route("**/api/demo/session", (route) => route.fulfill({
    status: 503, contentType: "application/json", body: JSON.stringify({ detail: "Sample temporarily unavailable. Try again." }),
  }));
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("alert")).toContainText("Sample temporarily unavailable");
  await expect(page.getByRole("button", { name: /try a sample day/i })).toBeEnabled();
  await page.unroute("**/api/demo/session");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
});

test("connected empty day stays truthful and keeps calendar controls", async ({ page }) => {
  await page.route("**/api/auth/status", (route) => route.fulfill({ json: {
    connected: true, email: "design@example.test", provider_available: true, requires_reconnect: false,
  } }));
  await page.route("**/api/day", (route) => route.fulfill({ json: {
    date: "2026-09-11", source_events: [], travel_blocks: [], decisions: [], last_run: null, label: "Your Google Calendar",
  } }));
  await page.route("**/api/activity", (route) => route.fulfill({ json: { receipts: [] } }));
  await page.route("**/api/me", (route) => route.fulfill({ json: {
    user_id: "design", time_zone: "Europe/London", source_calendar_id: "primary", glide_calendar_id: "primary",
    location_overrides: {}, mode: "driving", padding_minutes: 10, enabled: true, revision: 1,
  } }));
  await page.goto("/");
  await expect(page.getByText("A little open space.")).toBeVisible();
  await expect(page.getByText("Let’s connect the dots.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Disconnect", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reset sample" })).toHaveCount(0);
  await page.setViewportSize({ width: 320, height: 800 });
  await expectNoHorizontalOverflow(page);
});
