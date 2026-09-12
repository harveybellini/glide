import { expect, test } from "@playwright/test";

const BASE = process.env.PLAYWRIGHT_BASE_URL ?? "https://d3tvxy281s2u11.cloudfront.net";

test("probe: live site reachable and renders landing", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const response = await page.goto(BASE, { waitUntil: "domcontentloaded" });
  console.log("STATUS", response?.status());
  await expect(
    page.getByRole("button", { name: /Try a sample day/i }),
  ).toBeVisible();
  console.log("TITLE", await page.title());
  console.log("URL", page.url());
  console.log("CONSOLE_ERRORS", JSON.stringify(consoleErrors));
});
