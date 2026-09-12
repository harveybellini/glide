import { expect, test } from "@playwright/test";

test("judge path: sample day, conflict, resolve, no duplicates, reset", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Glide" })).toBeVisible();

  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();

  // First check: one travel block and one quantified decision.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toBeVisible();
  await expect(page.getByText(/shortfall: 10 minutes/i)).toBeVisible();
  // The card names the two appointments it is about, and a shortfall is a time
  // problem, so it must not offer a location correction.
  const decisionContext = page.locator("article.decision .decision-context");
  await expect(decisionContext).toBeVisible();
  await expect(decisionContext).toContainText(/school pickup/i);
  await expect(decisionContext).toContainText("→");
  await expect(
    page.getByRole("button", { name: /correct location/i }),
  ).toHaveCount(0);
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);
  await expect(
    page.getByText("Client visit → Appointment", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/buffer 10 min · applied/i)).toBeVisible();

  // Move the middle appointment through the inline editor.
  await page.getByRole("button", { name: "Edit" }).nth(1).click();
  const editor = page.locator("form.event-editor");
  await editor.getByLabel("Start", { exact: true }).fill("10:45");
  await editor.getByLabel("End", { exact: true }).fill("11:15");
  await page.getByRole("button", { name: /save changes/i }).click();
  await expect(
    page.getByText(/appointment updated/i),
  ).toBeVisible();

  // Recheck resolves the conflict into two blocks.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toHaveCount(0);
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(2);

  // A repeat run must not create duplicates.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible();
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(2);
  await expect(page.getByText("unchanged").first()).toBeVisible();

  // Reset returns to the starting state.
  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/no updates yet/i)).toBeVisible();
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(0);
});

test("skip link and labelled controls are reachable", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();

  await page.keyboard.press("Tab");
  await expect(page.locator(".skip-link")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#timeline")).toBeVisible();

  await expect(page.locator("section[aria-label='Calendar timeline']")).toBeVisible();
  await expect(page.getByRole("button", { name: "Settings" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reset sample" })).toBeVisible();
});

test("settings panel edits buffer and start address", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();

  await page.getByRole("button", { name: "Settings" }).click();
  const panel = page.locator("form.settings-panel");
  await panel.getByLabel("Arrival buffer (minutes)").fill("15");
  await panel.getByLabel("Start address").selectOption("place_b");
  await panel.getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/settings saved/i)).toBeVisible();
});

test("skipping a journey triggers a fresh run and stays skipped", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();

  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toBeVisible();

  await page.getByRole("button", { name: /skip this journey/i }).click();
  await expect(page.getByText(/journey skipped/i)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toHaveCount(0);
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

  // The skip survives later checks.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible();
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toHaveCount(0);
});

test("adding a shortfall journey anyway books it with a note", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();

  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toBeVisible();

  await page
    .getByLabel(/why you are adding this journey anyway/i)
    .fill("I can leave the earlier meeting early");
  await page.getByRole("button", { name: /add it anyway/i }).click();
  await expect(page.getByText(/travel added anyway/i)).toBeVisible();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toHaveCount(0);
  await expect(page.getByText(/^Travel . Glide$/)).toHaveCount(2);

  // The answer is durable: a later check does not raise the decision again.
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.getByText(/travel plan updated/i)).toBeVisible();
  await expect(page.getByText(/^Travel . Glide$/)).toHaveCount(2);
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toHaveCount(0);
});
