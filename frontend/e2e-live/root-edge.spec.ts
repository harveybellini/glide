import { expect, test } from "@playwright/test";

const BASE = "https://d3tvxy281s2u11.cloudfront.net";
const travel = (page: import("@playwright/test").Page) =>
  page.getByText("Travel · Glide", { exact: true });

test("buffer change must be honoured by later runs and replans", async ({ page }, testInfo) => {
  await page.goto(BASE);
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample workspace/i).first()).toBeVisible();

  // Set the buffer to 15 minutes BEFORE the first run (fits the 45-minute gap).
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const panel = page.locator("form.settings-panel");
  await panel.getByLabel("Arrival buffer (minutes)").fill("15");
  await panel.getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible();

  // Move the middle appointment so the day can be planned cleanly.
  await page.getByRole("button", { name: "Edit", exact: true }).nth(1).click();
  const editor = page.locator("form.event-editor");
  await editor.getByLabel("Start", { exact: true }).fill("10:45");
  await editor.getByLabel("End", { exact: true }).fill("11:15");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await expect(page.getByText(/appointment updated/i)).toBeVisible();

  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible({ timeout: 120000 });
  const firstBlocks = await travel(page).count();
  const firstText = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
  console.log("RUN1_BLOCKS", firstBlocks);
  console.log("RUN1_TIMELINE", firstText);
  console.log("RUN1_SAYS_15", /Buffer 15 min/.test(firstText));
  console.log("RUN1_ACTIVITY", (await page.locator("#activity").innerText()).replace(/\s+/g, " "));
  await page.screenshot({ path: testInfo.outputPath("01-run1-buffer15.png"), fullPage: true });

  // Now change the buffer to 5 and recheck: existing Glide blocks must follow.
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.locator("form.settings-panel").getByLabel("Arrival buffer (minutes)").fill("5");
  await page.locator("form.settings-panel").getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible();
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated|decision needs your input/i)).toBeVisible({
    timeout: 120000,
  });
  const secondText = (await page.locator("#timeline").innerText()).replace(/\s+/g, " ");
  console.log("RUN2_BLOCKS", await travel(page).count());
  console.log("RUN2_TIMELINE", secondText);
  console.log("RUN2_STILL_SAYS_15", /Buffer 15 min/.test(secondText));
  console.log("RUN2_SAYS_5", /Buffer 5 min/.test(secondText));
  console.log("RUN2_ACTIVITY", (await page.locator("#activity").innerText()).replace(/\s+/g, " "));
  console.log("RUN2_DECISION_CARD", await page
    .locator('section[aria-label="Needs your decision"]')
    .innerText()
    .then((t) => t.replace(/\s+/g, " "))
    .catch(() => "none"));
  await page.screenshot({ path: testInfo.outputPath("02-run2-buffer5.png"), fullPage: true });
});

test("editor input validation: empty, reversed, out-of-range, no location", async ({ page }) => {
  const errors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.goto(BASE);
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample workspace/i).first()).toBeVisible();

  await page.getByRole("button", { name: "Edit", exact: true }).first().click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible();
  console.log("EDITOR_HTML_LABELS", await editor.locator("label").allInnerTexts());
  console.log("EDITOR_INPUTS", await editor.locator("input").evaluateAll((nodes) =>
    nodes.map((node) => ({
      type: (node as HTMLInputElement).type,
      required: (node as HTMLInputElement).required,
      name: (node as HTMLInputElement).name,
      value: (node as HTMLInputElement).value,
    })),
  ));

  // Empty start
  await editor.getByLabel("Start", { exact: true }).fill("");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await page.waitForTimeout(800);
  console.log("EMPTY_START_EDITOR_STILL_OPEN", await editor.count());
  console.log("EMPTY_START_MESSAGES", await page
    .locator("form.event-editor .error, form.event-editor [role=alert], p.error, p.status")
    .allInnerTexts());
  console.log("EMPTY_START_VALIDITY", await editor
    .getByLabel("Start", { exact: true })
    .evaluate((node) => ({
      valid: (node as HTMLInputElement).validity.valid,
      message: (node as HTMLInputElement).validationMessage,
    })));

  // Reversed times
  await editor.getByLabel("Start", { exact: true }).fill("15:00");
  await editor.getByLabel("End", { exact: true }).fill("09:00");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await page.waitForTimeout(800);
  console.log("REVERSED_EDITOR_STILL_OPEN", await editor.count());
  console.log("REVERSED_MESSAGES", await page
    .locator("form.event-editor .error, form.event-editor [role=alert], p.error, p.status")
    .allInnerTexts());
  console.log("REVERSED_TIMELINE", (await page.locator("#timeline").innerText()).replace(/\s+/g, " "));

  // Out-of-range value forced in
  await editor.getByLabel("Start", { exact: true }).fill("25:00").catch(() => {});
  console.log("OUT_OF_RANGE_VALUE", await editor
    .getByLabel("Start", { exact: true })
    .inputValue()
    .catch((e) => `unfillable: ${e.message.split("\n")[0]}`));

  // Location cleared
  await editor.getByLabel("Start", { exact: true }).fill("09:00");
  await editor.getByLabel("End", { exact: true }).fill("10:00");
  await editor.getByLabel("Location").fill("");
  await editor.getByRole("button", { name: /save changes/i }).click();
  await page.waitForTimeout(1200);
  console.log("CLEARED_LOCATION_TIMELINE", (await page.locator("#timeline").innerText()).replace(/\s+/g, " "));
  console.log("CLEARED_LOCATION_STATUS", await page.locator("p.status").innerText());

  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated|decision needs your input/i)).toBeVisible({
    timeout: 120000,
  });
  console.log("AFTER_CLEARED_LOCATION_DECISIONS", await page
    .locator('section[aria-label="Needs your decision"]')
    .innerText()
    .then((t) => t.replace(/\s+/g, " "))
    .catch(() => "none"));
  console.log("CONSOLE_ERRORS", JSON.stringify(errors));
});
