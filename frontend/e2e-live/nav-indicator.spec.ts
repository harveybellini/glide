// Live check for the sidebar section indicator. The deployed bundle used to
// hardcode `nav-current` on "My day", so the marker never moved when Settings
// was opened or Activity was scrolled into view.
import { expect, test } from "@playwright/test";
import { createSession, openWithSession } from "./harness";

test("sidebar indicator follows the section on the deployed site", async ({ page }) => {
  const sessionId = await createSession();
  await openWithSession(page, sessionId);

  const nav = page.getByRole("navigation", { name: /workspace/i });
  await expect(nav).toBeVisible();
  const dayLink = nav.getByRole("link", { name: /my day/i });
  const activityLink = nav.getByRole("link", { name: /activity/i });
  const settingsButton = nav.getByRole("button", { name: /settings/i });

  await expect(dayLink).toHaveClass(/nav-current/);
  await expect(dayLink).toHaveAttribute("aria-current", "page");

  await settingsButton.click();
  await expect(settingsButton).toHaveClass(/nav-current/);
  await expect(dayLink).not.toHaveClass(/nav-current/);

  await page.keyboard.press("Escape");
  await expect(dayLink).toHaveClass(/nav-current/);

  await activityLink.click();
  await expect(activityLink).toHaveClass(/nav-current/);
  await expect(activityLink).toHaveAttribute("aria-current", "page");
  await expect(dayLink).not.toHaveClass(/nav-current/);

  await dayLink.click();
  await expect(dayLink).toHaveClass(/nav-current/);
  await expect(activityLink).not.toHaveClass(/nav-current/);
});
