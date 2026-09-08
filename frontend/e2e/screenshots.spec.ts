import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const directory = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(directory, "../../submission/screenshots");

test("capture the four gallery screenshots", async ({ page }) => {
  await page.goto("/");
  await page.screenshot({ path: path.join(output, "01-landing.png") });

  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(
    page.getByRole("heading", { name: /needs your decision/i }),
  ).toBeVisible();

  const timeline = page.locator("section[aria-label='Calendar timeline']");
  await timeline.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "02-timeline.png") });

  const decisions = page.locator("section[aria-label='Needs your decision']");
  await decisions.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "03-decision.png") });

  const activity = page.locator("section[aria-label='Activity']");
  await activity.scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, "04-activity.png") });
});
