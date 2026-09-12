import { expect, test } from "@playwright/test";

// Baseline reachability probe used before fan-out. Kept small on purpose.
test("smoke: live site loads and sample day starts", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  console.log("STATUS", response?.status());
  console.log("TITLE", await page.title());

  const start = page.getByRole("button", { name: /try a sample day/i });
  await expect(start).toBeVisible();
  await start.click();

  await expect(page.getByText(/sample calendar/i).first()).toBeVisible();
  console.log("DAY_LABEL_OK");
  console.log("CONSOLE_ERRORS", JSON.stringify(consoleErrors));
});
