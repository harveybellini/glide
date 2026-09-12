import { expect, test, type Page } from "@playwright/test";

const RUN_TIMEOUT = 45_000;

// The live audit runs concurrently with other agents that use the default
// output directory, and Playwright wipes it on start. Traces would be deleted
// out from under the run, so this spec keeps its evidence in the log instead.
test.use({ trace: "off" });

const diagnostics: string[] = [];

test.beforeEach(async ({ page }) => {
  diagnostics.length = 0;
  page.on("response", (response) => {
    if (response.url().includes("/api/") && response.status() >= 400) {
      diagnostics.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    }
  });
  page.on("requestfailed", (request) => {
    if (request.url().includes("/api/")) {
      diagnostics.push(`requestfailed ${request.method()} ${request.url()} :: ${request.failure()?.errorText ?? "unknown"}`);
    }
  });
  page.on("pageerror", (error) => diagnostics.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") diagnostics.push(`console.error: ${message.text()}`);
  });
});

test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) return;
  let body = "";
  try {
    body = (await page.locator("body").innerText()).replace(/\s*\n\s*/g, " | ").slice(0, 1500);
  } catch (error) {
    body = `body unavailable: ${String(error)}`;
  }
  console.log(
    [
      "--- live failure diagnostics ---",
      `test: ${testInfo.title}`,
      `url: ${page.url()}`,
      `api/console: ${diagnostics.length ? diagnostics.join(" ;; ") : "none"}`,
      `body: ${body}`,
      "--- end diagnostics ---",
    ].join("\n"),
  );
});

async function startSample(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample workspace/i).first()).toBeVisible();
  await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
}

async function recheck(page: Page) {
  await page.getByRole("button", { name: "Recheck now" }).click();
  await expect(
    page.getByText(/travel plan updated|decision needs your input/i).first(),
  ).toBeVisible({ timeout: RUN_TIMEOUT });
}

test.describe("live full-control audit", () => {
  test("landing controls and skip links work", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /connect google calendar/i })).toBeVisible();
    await expect(page.getByRole("link", { name: /how it works/i })).toBeVisible();

    await page.getByRole("link", { name: /how it works/i }).click();
    await expect(page).toHaveURL(/#how-it-works$/);
    await expect(
      page.getByRole("heading", { name: /a calendar that connects the dots/i }),
    ).toBeVisible();

    await page.goto("/", { waitUntil: "domcontentloaded" });
    // The app is a client-rendered SPA: a Tab pressed before the first render
    // lands nowhere, so wait for the hero and retry once if focus misses.
    await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await page.keyboard.press("Tab");
      const onSkipLink = await page
        .locator(".skip-link")
        .evaluate((element) => element === document.activeElement);
      if (onSkipLink) break;
    }
    await expect(page.locator(".skip-link")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#get-started$/);
    await expect(page.locator("#get-started")).toBeFocused();

    const startResponse = await page.request.get("/api/auth/google/start", {
      maxRedirects: 0,
    });
    expect(startResponse.status()).toBe(307);
    expect(startResponse.headers()["location"]).toContain("accounts.google.com");
    expect(pageErrors).toEqual([]);
  });

  test("workspace navigation, settings panel, and reset work", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await startSample(page);

    await page.getByRole("link", { name: /activity/i }).click();
    await expect(page).toHaveURL(/#activity$/);
    await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();

    await page.getByRole("link", { name: /my day/i }).click();
    await expect(page).toHaveURL(/#timeline$/);
    await expect(page.locator("#timeline")).toBeInViewport();

    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    await expect(settingsButton).toHaveAttribute("aria-expanded", "true");
    const settingsPanel = page.getByRole("form", { name: "Travel settings" });
    await expect(settingsPanel).toBeVisible();
    await expect(page.getByLabel("Arrival buffer (minutes)")).toBeFocused();

    await settingsPanel.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(settingsPanel).toHaveCount(0);
    await expect(settingsButton).toBeFocused();

    await settingsButton.click();
    await page.keyboard.press("Escape");
    await expect(settingsPanel).toHaveCount(0);
    await expect(settingsButton).toBeFocused();

    await page.getByRole("button", { name: "Reset sample", exact: true }).click();
    await expect(page.getByText("Sample reset to its starting state.")).toBeVisible();
    await expect(page.getByText("No updates yet. Run a check to see changes here.")).toBeVisible();
    await expect(page.getByText("Travel Â· Glide", { exact: true })).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });

  test("pause and resume controls update automation state", async ({ page }) => {
    await startSample(page);

    await page.getByRole("button", { name: "Pause automation" }).click();
    await expect(page.getByText("Glide is paused")).toBeVisible();
    await expect(page.getByRole("button", { name: "Resume automation" })).toBeVisible();
    await expect(page.getByText("Resume when you’re ready to plan.")).toBeVisible();

    await page.getByRole("button", { name: "Resume automation" }).click();
    await expect(page.getByText("Glide is on")).toBeVisible();
    await expect(page.getByRole("button", { name: "Pause automation" })).toBeVisible();
    await expect(
      page.getByText("A little help between appointments."),
    ).toBeVisible();

    await page.getByRole("button", { name: "Pause automation" }).click();
    await recheck(page);
    await expect(page.getByRole("button", { name: "Resume automation" })).toBeVisible();
  });

  test("settings inputs and select options save and persist", async ({ page }) => {
    await startSample(page);

    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    const panel = page.getByRole("form", { name: "Travel settings" });

    await panel.getByLabel("Arrival buffer (minutes)").fill("15");
    await panel.getByLabel("Earliest departure (optional)").fill("07:45");
    await panel.getByLabel("Start address").selectOption("place_b");
    await panel.getByLabel("Time zone").selectOption("Europe/Paris");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    await expect(panel).toHaveCount(0);
    await expect(settingsButton).toBeFocused();
    await expect(page.getByText("15 min").first()).toBeVisible();
    await expect(
      page.locator(".note-footer").getByText("Westfield Surgery"),
    ).toBeVisible();

    await settingsButton.click();
    await expect(panel.getByLabel("Arrival buffer (minutes)")).toHaveValue("15");
    await expect(panel.getByLabel("Earliest departure (optional)")).toHaveValue(
      "07:45:00",
    );
    await expect(panel.getByLabel("Start address")).toHaveValue("place_b");
    await expect(panel.getByLabel("Time zone")).toHaveValue("Europe/Paris");

    const buffer = panel.getByLabel("Arrival buffer (minutes)");
    for (const invalid of ["61", "99", "-1"]) {
      await buffer.fill(invalid);
      await panel.getByRole("button", { name: "Save settings" }).click();
      expect(
        await buffer.evaluate(
          (element: HTMLInputElement) => element.validationMessage,
        ),
      ).not.toBe("");
      await expect(panel).toBeVisible();
    }

    await buffer.fill("0");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    await expect(page.getByText("0 min").first()).toBeVisible();
  });

  test("event editor cancel, validation, save, and persistence work", async ({ page }) => {
    await startSample(page);

    const editButtons = page.getByRole("button", { name: "Edit", exact: true });
    await expect(editButtons).toHaveCount(3);
    const middleEdit = editButtons.nth(1);
    await middleEdit.click();
    const editor = page.locator("form.event-editor");
    await expect(editor).toBeVisible();
    await expect(editor.getByLabel("Start", { exact: true })).toBeFocused();

    await editor.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(editor).toHaveCount(0);
    await expect(middleEdit).toBeFocused();

    await middleEdit.click();
    await editor.getByLabel("Start", { exact: true }).fill("11:00");
    await editor.getByLabel("End", { exact: true }).fill("10:30");
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(editor.getByRole("alert")).toHaveText(
      "End time must be after start time.",
    );

    await editor.getByLabel("Start", { exact: true }).fill("10:45");
    await editor.getByLabel("End", { exact: true }).fill("11:15");
    await editor.getByLabel("Location", { exact: true }).fill("Updated test location");
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page.getByText("Appointment updated. Recheck to replan travel."),
    ).toBeVisible();
    await expect(editor).toHaveCount(0);
    await expect(page.getByText("Updated test location")).toBeVisible();
    await expect(middleEdit).toBeFocused();

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByText("Updated test location")).toBeVisible();
  });

  test("decision actions scroll, skip, and remain skipped after recheck", async ({
    page,
  }) => {
    await startSample(page);
    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toBeVisible({ timeout: RUN_TIMEOUT });
    await expect(page.getByText(/shortfall: 10 minutes/i)).toBeVisible();
    await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

    await page.getByRole("button", { name: "Edit appointments" }).click();
    await expect(page.locator("#timeline")).toBeInViewport();

    await page.getByRole("button", { name: "Skip this journey" }).click();
    await expect(page.getByText("Journey skipped.")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toHaveCount(0);
    await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

    await recheck(page);
    await expect(page.getByText("Travel plan updated.")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toHaveCount(0);
    await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);
  });

  test("sample session survives reload and reset removes generated state", async ({
    page,
  }) => {
    await startSample(page);
    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toBeVisible({ timeout: RUN_TIMEOUT });

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toBeVisible();
    await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

    await page.getByRole("button", { name: "Reset sample", exact: true }).click();
    await expect(page.getByText("No updates yet. Run a check to see changes here.")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toHaveCount(0);
    await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(0);
  });

  test("the complete happy path stays free of console and network failures", async ({
    page,
  }) => {
    const consoleErrors: string[] = [];
    const pageErrors: string[] = [];
    const failedRequests: string[] = [];
    const httpErrors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) =>
      failedRequests.push(
        `${request.method()} ${request.url()} ${request.failure()?.errorText ?? ""}`,
      ),
    );
    page.on("response", (response) => {
      if (response.status() >= 400) {
        httpErrors.push(
          `${response.status()} ${response.request().method()} ${response.url()}`,
        );
      }
    });

    await startSample(page);
    await page.getByRole("button", { name: "Pause automation" }).click();
    await page.getByRole("button", { name: "Resume automation" }).click();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Edit", exact: true }).first().click();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(
      page.getByRole("heading", { name: "Needs your decision" }),
    ).toBeVisible({ timeout: RUN_TIMEOUT });
    await page.getByRole("button", { name: "Skip this journey" }).click();
    await expect(page.getByText("Journey skipped.")).toBeVisible();
    await page.getByRole("button", { name: "Reset sample", exact: true }).click();
    await expect(page.getByText("Sample reset to its starting state.")).toBeVisible();

    console.log("console errors:", JSON.stringify(consoleErrors));
    console.log("page errors:", JSON.stringify(pageErrors));
    console.log("failed requests:", JSON.stringify(failedRequests));
    console.log("HTTP >=400:", JSON.stringify(httpErrors));
    expect(consoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
    expect(failedRequests).toEqual([]);
    expect(httpErrors).toEqual([]);
  });
});
