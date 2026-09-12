import { expect, test } from "@playwright/test";

async function expectNoHorizontalOverflow(page: import("@playwright/test").Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
}

/** Palette and type contracts from docs/frontend-style-guide.md. */
const TOKENS = {
  canvas: "rgb(248, 247, 242)",
  surface: "rgb(255, 254, 250)",
  ink: "rgb(38, 61, 52)",
  muted: "rgb(104, 113, 105)",
  green: "rgb(47, 89, 70)",
  sage: "rgb(230, 236, 223)",
  line: "rgb(220, 224, 213)",
  amberBg: "rgb(250, 240, 217)",
  amberInk: "rgb(120, 84, 29)",
  amberLine: "rgb(221, 202, 162)",
  focus: "rgb(146, 100, 45)",
  inputBorder: "rgb(138, 151, 131)",
  serif: "Georgia",
  sans: "Segoe UI",
} as const;

function styleOf(page: import("@playwright/test").Page, selector: string) {
  return page.evaluate((target: string) => {
    const element = document.querySelector(target);
    if (!element) return null;
    const style = getComputedStyle(element);
    return {
      fontFamily: style.fontFamily,
      fontSize: parseFloat(style.fontSize),
      color: style.color,
      background: style.backgroundColor,
      radius: style.borderRadius,
      borderColor: style.borderTopColor,
      borderLeftColor: style.borderLeftColor,
      borderLeftWidth: style.borderLeftWidth,
      minHeight: parseFloat(style.minHeight) || 0,
      height: element.getBoundingClientRect().height,
      fontVariantNumeric: style.fontVariantNumeric,
    };
  }, selector);
}

async function expectSampleDay(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar|sample workspace/i).first()).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible({ timeout: 45_000 });
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
  // Each timeline Edit button is named after the appointment it edits, so the
  // accessible names are unique (a11y fix); match the shared prefix instead.
  const edit = page.getByRole("button", { name: /^Edit / }).first();
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

test("style guide tokens apply to the welcome page", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();

  const shell = await page.evaluate(() => ({
    themeColor: document.querySelector('meta[name="theme-color"]')?.getAttribute("content"),
    icon: document.querySelector('link[rel="icon"]')?.getAttribute("href") ?? "",
  }));
  expect(shell.themeColor).toBe("#2f5946");
  expect(shell.icon).toContain("image/svg+xml");

  const heading = await styleOf(page, ".hero h2");
  expect(heading?.fontFamily).toContain(TOKENS.serif);
  expect(heading?.color).toBe(TOKENS.ink);
  expect(heading?.fontSize).toBeGreaterThanOrEqual(48);

  const copy = await styleOf(page, ".hero-description");
  expect(copy?.fontFamily).toContain(TOKENS.sans);
  expect(copy?.fontSize).toBeGreaterThanOrEqual(12);

  const primary = await styleOf(page, ".hero-actions .primary");
  expect(primary?.background).toBe(TOKENS.green);
  expect(primary?.color).toBe(TOKENS.surface);
  expect(primary?.radius).toBe("8px");
  expect(primary?.height).toBeGreaterThanOrEqual(44);

  for (const selector of [".hero .eyebrow", ".preview-caption"]) {
    const eyebrow = await styleOf(page, selector);
    expect(eyebrow?.fontSize, `${selector} stays inside the documented 10-12px eyebrow range`).toBeGreaterThanOrEqual(10);
    expect(eyebrow?.fontSize).toBeLessThanOrEqual(12);
  }

  const focus = await page.evaluate(() => {
    const element = document.querySelector(".hero-actions .primary") as HTMLElement;
    element.focus();
    const style = getComputedStyle(element);
    return { color: style.outlineColor, width: style.outlineWidth, line: style.outlineStyle };
  });
  expect(focus.color).toBe(TOKENS.focus);
  expect(focus.line).toBe("solid");
  expect(parseFloat(focus.width)).toBeGreaterThanOrEqual(2);
});

test("welcome connection actions follow the secondary button contract", async ({ page }) => {
  await page.route("**/api/auth/status", (route) => route.fulfill({ json: {
    connected: false, provider_available: true, requires_reconnect: false,
  } }));
  await page.goto("/");
  await expect(page.getByRole("link", { name: /connect google calendar/i })).toBeVisible();

  const connect = await styleOf(page, ".connection .button-link");
  expect(connect?.background).toBe(TOKENS.surface);
  expect(connect?.borderColor).toBe(TOKENS.line);
  expect(connect?.color).toBe(TOKENS.ink);
  expect(connect?.radius).toBe("8px");
  expect(connect?.height).toBeGreaterThanOrEqual(44);

  // "Try a sample day" stays the only primary action on the welcome page.
  expect(await page.locator(".landing .primary").count()).toBe(1);
});

test("reconnect prompt appears inside the daily workspace controls", async ({ page }) => {
  await page.route("**/api/auth/status", (route) => route.fulfill({ json: {
    connected: true, email: "design@example.test", provider_available: true, requires_reconnect: true,
  } }));
  await page.route("**/api/day", (route) => route.fulfill({ json: {
    date: "2026-09-11", source_events: [], travel_blocks: [], decisions: [], last_run: null,
    label: "Your Google Calendar",
  } }));
  await page.route("**/api/activity", (route) => route.fulfill({ json: { receipts: [] } }));
  await page.route("**/api/me", (route) => route.fulfill({ json: {
    user_id: "design", time_zone: "Europe/London", source_calendar_id: "primary", glide_calendar_id: "primary",
    location_overrides: {}, start_place: null, earliest_departure: null, mode: "driving",
    padding_minutes: 10, enabled: true, revision: 1,
  } }));

  await page.goto("/");
  await expect(page.getByText(/needs updated event-write permission/i)).toBeVisible();
  await expect(page.getByRole("link", { name: /reconnect google calendar/i })).toBeVisible();

  const reconnect = await styleOf(page, ".account-bar .button-link");
  expect(reconnect?.background).toBe(TOKENS.green);
  expect(reconnect?.color).toBe(TOKENS.surface);
  expect(reconnect?.height).toBeGreaterThanOrEqual(36);
});

test("style guide tokens apply to the day, settings, and editor surfaces", async ({ page }) => {
  await expectSampleDay(page);

  const canvas = await page.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
  expect(canvas).toBe(TOKENS.canvas);

  const title = await styleOf(page, ".day-heading h2");
  expect(title?.fontFamily).toContain(TOKENS.serif);
  expect(title?.color).toBe(TOKENS.ink);

  const stat = await styleOf(page, ".stat-value");
  expect(stat?.fontFamily).toContain(TOKENS.serif);
  expect(stat?.fontVariantNumeric).toContain("tabular-nums");

  const event = await styleOf(page, ".row.event");
  expect(event?.background).toBe(TOKENS.surface);
  expect(event?.borderColor).toBe(TOKENS.line);
  expect(event?.radius).toBe("10px");

  const time = await styleOf(page, ".row .time");
  expect(time?.fontVariantNumeric).toContain("tabular-nums");

  for (const selector of [
    ".workspace-header > .eyebrow",
    ".section-heading .eyebrow",
    ".journey-note .eyebrow",
    ".quiet-note .eyebrow",
  ]) {
    const eyebrow = await styleOf(page, selector);
    expect(eyebrow?.fontSize, `${selector} stays inside the documented 10-12px eyebrow range`).toBeGreaterThanOrEqual(10);
  }

  const note = await styleOf(page, ".journey-note");
  expect(note?.radius).toBe("16px");

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const panel = await styleOf(page, "form.settings-panel");
  expect(panel?.background).toBe(TOKENS.surface);
  expect(panel?.radius).toBe("16px");
  const field = await styleOf(page, "form.settings-panel input[type=number]");
  expect(field?.borderColor).toBe(TOKENS.inputBorder);
  expect(field?.radius).toBe("6px");
  expect(field?.height).toBeGreaterThanOrEqual(44);
  const save = await styleOf(page, "form.settings-panel .primary");
  expect(save?.background).toBe(TOKENS.green);
  expect(save?.color).toBe(TOKENS.surface);
  const cancel = await styleOf(page, "form.settings-panel .panel-actions button:not(.primary)");
  expect(cancel?.background).toBe(TOKENS.surface);
  expect(cancel?.borderColor).toBe(TOKENS.line);
  await page.keyboard.press("Escape");

  await page.getByRole("button", { name: /^Edit / }).first().click();
  const editor = await styleOf(page, "form.event-editor input[type=time]");
  expect(editor?.borderColor).toBe(TOKENS.inputBorder);
  expect(editor?.height).toBeGreaterThanOrEqual(44);
  const editorPanel = await styleOf(page, "form.event-editor");
  expect(editorPanel?.background).toBe(TOKENS.surface);
  expect(editorPanel?.radius).toBe("16px");
  await page.keyboard.press("Escape");
});

test("style guide tokens apply to travel blocks, decisions, and activity", async ({ page }) => {
  await expectSampleDay(page);
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByRole("heading", { name: /needs your decision/i })).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

  const travel = await styleOf(page, ".row.travel");
  expect(travel?.background).toBe(TOKENS.sage);
  const marker = await styleOf(page, ".row.travel .content");
  expect(marker?.borderLeftColor).toBe(TOKENS.green);
  expect(parseFloat(marker?.borderLeftWidth ?? "0")).toBeGreaterThanOrEqual(3);
  const travelTitle = await styleOf(page, ".row.travel .content strong");
  expect(travelTitle?.color).toBe("rgb(35, 69, 52)");

  const decisions = await styleOf(page, ".decisions");
  expect(decisions?.background).toBe(TOKENS.amberBg);
  expect(decisions?.color).toBe(TOKENS.amberInk);
  expect(decisions?.radius).toBe("16px");
  const decisionButton = await styleOf(page, ".decision-actions button");
  expect(decisionButton?.height).toBeGreaterThanOrEqual(44);
  expect(decisionButton?.background).toBe(TOKENS.surface);

  await expect(page.getByText("applied").first()).toBeVisible();
  const badge = await styleOf(page, ".badge");
  expect(badge?.background).toBe(TOKENS.sage);
  expect(badge?.color).toBe(TOKENS.green);
  const receiptTime = await styleOf(page, ".activity time");
  expect(receiptTime?.fontVariantNumeric).toContain("tabular-nums");
});

test("live-mode decision actions use the same control contract", async ({ page }) => {
  const event = (id: string, start: string, end: string, title: string) => ({
    provider_event_id: id, occurrence_id: id, calendar_id: "primary", etag: "e1",
    start, end, original_time_zone: "Europe/London", title, location: "Somewhere",
    place_id: null, status: "confirmed", transparency: "opaque", attendance: "in_person", kind: "appointment",
  });
  await page.route("**/api/auth/status", (route) => route.fulfill({ json: {
    connected: true, email: "design@example.test", provider_available: true, requires_reconnect: false,
  } }));
  await page.route("**/api/day", (route) => route.fulfill({ json: {
    date: "2026-09-11", label: "Your Google Calendar",
    source_events: [
      event("a", "2026-09-11T09:00:00+01:00", "2026-09-11T10:00:00+01:00", "Client visit"),
      event("b", "2026-09-11T10:30:00+01:00", "2026-09-11T11:30:00+01:00", "Appointment"),
    ],
    travel_blocks: [],
    decisions: [{
      id: "d1", user_id: "live", occurrence_id: "b", journey_key: "a->b", source_revision: "r1",
      reason: "insufficient_time",
      calculated_facts: { available_seconds: 1800, required_seconds: 2400, shortfall_seconds: 600 },
      allowed_actions: ["correct_location", "edit_source_event", "skip_journey"], status: "open", version: 1,
    }],
    last_run: null,
  } }));
  await page.route("**/api/activity", (route) => route.fulfill({ json: { receipts: [] } }));
  await page.route("**/api/me", (route) => route.fulfill({ json: {
    user_id: "live", time_zone: "Europe/London", source_calendar_id: "primary", glide_calendar_id: "primary",
    location_overrides: {}, start_place: null, earliest_departure: null, mode: "driving",
    padding_minutes: 10, enabled: true, revision: 3,
  } }));

  await page.goto("/");
  await expect(page.getByText("Your Google Calendar").first()).toBeVisible();

  const calendarLink = await styleOf(page, ".decision-actions a[href*='calendar.google.com']");
  expect(calendarLink?.background).toBe(TOKENS.surface);
  expect(calendarLink?.color).toBe(TOKENS.ink);
  expect(calendarLink?.radius).toBe("8px");
  expect(calendarLink?.borderColor).toBe(TOKENS.amberLine);
  expect(calendarLink?.height).toBeGreaterThanOrEqual(44);

  const search = await styleOf(page, ".decision-actions .search-row input");
  expect(search?.borderColor).toBe(TOKENS.inputBorder);
  expect(search?.height).toBeGreaterThanOrEqual(44);

  const disconnect = await styleOf(page, ".account-bar button");
  expect(disconnect?.height).toBeGreaterThanOrEqual(36);
  await expect(page.getByText("Google Calendar", { exact: true })).toBeVisible();
});

test("layout rules hold at the documented breakpoints and at 200% zoom", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar|sample workspace/i).first()).toBeVisible();

  const columns = (selector: string) =>
    page.evaluate((target: string) => {
      const element = document.querySelector(target);
      return element ? getComputedStyle(element).gridTemplateColumns.split(" ").length : 0;
    }, selector);
  const display = (selector: string) =>
    page.evaluate((target: string) => {
      const element = document.querySelector(target);
      return element ? getComputedStyle(element).display : "";
    }, selector);

  await page.setViewportSize({ width: 1440, height: 900 });
  expect(await columns(".day-layout")).toBe(2);

  await page.setViewportSize({ width: 901, height: 900 });
  expect(await columns(".day-layout")).toBe(2);
  await page.setViewportSize({ width: 900, height: 900 });
  expect(await columns(".day-layout")).toBe(1);
  expect(await columns(".insights")).toBe(2);

  await page.setViewportSize({ width: 768, height: 900 });
  expect(await columns(".day-layout")).toBe(1);
  expect(await columns(".insights")).toBe(2);

  await page.setViewportSize({ width: 680, height: 900 });
  expect(await display(".app")).toBe("block");
  expect(await columns(".day-stats")).toBe(2);
  expect(await columns(".insights")).toBe(1);
  const compactEyebrow = await styleOf(page, ".workspace-header > .eyebrow");
  expect(compactEyebrow?.fontSize).toBeGreaterThanOrEqual(10);

  await page.setViewportSize({ width: 390, height: 844 });
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: /^Edit / }).first().click();
  expect(await columns(".field-grid")).toBe(2);
  await page.setViewportSize({ width: 360, height: 800 });
  expect(await columns(".field-grid")).toBe(1);
  await page.keyboard.press("Escape");
  await expectNoHorizontalOverflow(page);

  await page.setViewportSize({ width: 640, height: 900 });
  await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.evaluate(() => { document.documentElement.style.zoom = "1"; });
});
