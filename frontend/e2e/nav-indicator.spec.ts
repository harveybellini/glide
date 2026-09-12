import { expect, test } from "@playwright/test";

/**
 * Regression: the sidebar hardcoded `nav-current` (and aria-current="page") on
 * the "My day" link, so the indicator never moved when Settings was opened or
 * when Activity was scrolled into view.
 */
test("the sidebar indicator follows the section in view", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample calendar|sample workspace/i).first()).toBeVisible();

  const nav = page.getByRole("navigation", { name: /workspace/i });
  const dayLink = nav.getByRole("link", { name: /my day/i });
  const activityLink = nav.getByRole("link", { name: /activity/i });
  const settingsButton = nav.getByRole("button", { name: /settings/i });

  await expect(dayLink).toHaveClass(/nav-current/);
  await expect(dayLink).toHaveAttribute("aria-current", "page");

  await settingsButton.click();
  await expect(settingsButton).toHaveClass(/nav-current/);
  await expect(dayLink).not.toHaveClass(/nav-current/);
  await expect(dayLink).not.toHaveAttribute("aria-current", "page");

  await page.keyboard.press("Escape");
  await expect(dayLink).toHaveClass(/nav-current/);
  await expect(settingsButton).not.toHaveClass(/nav-current/);

  await activityLink.click();
  await expect(activityLink).toHaveClass(/nav-current/);
  await expect(activityLink).toHaveAttribute("aria-current", "page");
  await expect(dayLink).not.toHaveClass(/nav-current/);

  await dayLink.click();
  await expect(dayLink).toHaveClass(/nav-current/);
  await expect(activityLink).not.toHaveClass(/nav-current/);
});
