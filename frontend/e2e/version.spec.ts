import { expect, test } from "@playwright/test";

// The footer is the version monitor: it names the build this tab is running and
// checks the host's version.json for a newer one. The dev server publishes the
// same manifest a production build does, so "up to date" is the honest answer
// here and a phantom update notice would be a real bug.
test("the footer reports the running version and checks for a newer deploy", async ({
  page,
}) => {
  await page.goto("/");

  const badge = page.getByRole("button", { name: /version details/i });
  await expect(badge).toBeVisible();
  await expect(badge).toContainText(/v\d+\.\d+\.\d+/);

  await badge.click();
  const panel = page.locator("#version-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText(/this tab/i);
  await expect(panel).toContainText(/up to date/i);
  await expect(panel.locator(".version-row", { hasText: "API" })).toContainText(
    /v?\d+\.\d+\.\d+/,
  );
  await expect(page.getByText(/a new version of glide is ready/i)).toHaveCount(0);

  await page.getByRole("button", { name: /check now/i }).click();
  await expect(panel).toContainText(/up to date/i);

  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
});

// A tab left open across a deploy must say so, and "Later" must not silence
// the next deploy.
test("an open tab is told when a newer build is deployed", async ({ page }) => {
  await page.route("**/version.json", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        version: "9.9.9",
        commit: "newbuild",
        builtAt: new Date().toISOString(),
        mode: "production",
      }),
    }),
  );
  await page.goto("/");

  const notice = page.getByText(/a new version of glide is ready/i);
  await expect(notice).toBeVisible();
  await expect(page.locator(".update-banner")).toContainText(/9\.9\.9/);

  await page.getByRole("button", { name: "Later" }).click();
  await expect(notice).toHaveCount(0);

  // The badge still reports the found deploy after the notice is dismissed.
  await page.getByRole("button", { name: /version details/i }).click();
  await expect(page.locator("#version-panel")).toContainText(/9\.9\.9/);
});
