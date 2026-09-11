import { expect, test } from "@playwright/test";

/**
 * S11/F15: `request()` in src/api.ts may retry a transient transport failure,
 * but only for idempotent verbs. A retried POST /api/runs would create a
 * second run row and queue message.
 */

test("a failed POST /api/runs is attempted exactly once", async ({ page }) => {
  const created = await page.request.post("/api/demo/session", { data: {} });
  expect(created.ok()).toBeTruthy();
  const sessionId = (await created.json()).session.session_id as string;

  await page.addInitScript((id: string) => {
    window.localStorage.setItem("glide-sample-session", id);
  }, sessionId);

  let attempts = 0;
  await page.route("**/api/runs", async (route) => {
    attempts += 1;
    await route.abort("failed");
  });

  await page.goto("/");
  await expect(page.getByRole("button", { name: "Recheck now" })).toBeVisible();
  await page.getByRole("button", { name: "Recheck now" }).click();

  await expect(page.getByRole("alert")).toBeVisible();
  expect(attempts).toBe(1);
});
