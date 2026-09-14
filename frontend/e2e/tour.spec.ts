import { expect, test } from "@playwright/test";

/**
 * The guided tour is the first thing a visitor meets, so these checks start
 * from a profile that has never seen it: the shared config marks it seen for
 * every other spec (see playwright.config.ts).
 */
test.use({ storageState: { cookies: [], origins: [] } });

test("a first-time visitor is shown what to click and walked into the day", async ({
  page,
}) => {
  await page.goto("/");

  const first = page.getByRole("dialog", { name: /start with a sample day/i });
  await expect(first).toBeVisible();
  await expect(first).toContainText("STEP 1 OF 7");

  // The spotlight is drawn around the button the step is talking about. The
  // ring follows the target, so poll rather than sampling one frame of it.
  await expect
    .poll(async () => {
      const ring = await page.locator(".tour-ring").boundingBox();
      const target = await page
        .getByRole("button", { name: /try a sample day/i })
        .boundingBox();
      if (!ring || !target) {
        return Number.POSITIVE_INFINITY;
      }
      return Math.max(
        Math.abs(ring.x - (target.x - 6)),
        Math.abs(ring.y - (target.y - 6)),
      );
    }, { message: "the spotlight sits on the highlighted button" })
    .toBeLessThan(2);

  // The tour is not modal: the highlighted control still takes the click.
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("button", { name: "Recheck now" })).toBeVisible();

  const second = page.getByRole("dialog", { name: /it works while you are away/i });
  await expect(second).toBeVisible();
  await second.getByRole("button", { name: "Next" }).click();

  const third = page.getByRole("dialog", { name: /recheck now, if you want it sooner/i });
  await expect(third).toBeVisible();
  await third
    .getByRole("button", { name: /run a check now/i })
    .click();

  // The step that follows is only honest once the run has written a block.
  await expect(
    page.getByRole("dialog", { name: /see the time you were missing/i }),
  ).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText("Travel · Glide", { exact: true })).toHaveCount(1);

  await page.getByRole("button", { name: "Next" }).click();
  await expect(
    page.getByRole("dialog", { name: /when it does not fit/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(
    page.getByRole("dialog", { name: /every change is on the record/i }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();

  const last = page.getByRole("dialog", { name: /you keep the final say/i });
  await expect(last).toBeVisible();
  await last.getByRole("button", { name: "Finish" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  // Finishing is remembered, so the next visit is not interrupted.
  await page.reload();
  await expect(page.getByRole("button", { name: "Recheck now" })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("a visitor who has seen it can ask for it again, and leave with Escape", async ({
  page,
}) => {
  await page.addInitScript(() =>
    window.localStorage.setItem("glide-tour-v2", "done"),
  );
  await page.goto("/");

  const replay = page.getByRole("button", { name: /show me around/i });
  await expect(replay).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await replay.click();
  const tour = page.getByRole("dialog", { name: /start with a sample day/i });
  await expect(tour).toBeVisible();
  await expect(
    tour.getByRole("button", { name: /start the sample day/i }),
  ).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(replay).toBeFocused();
});

test("the tour card stays on a phone screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/?tour=1");

  const card = page.locator(".tour-card");
  await expect(card).toBeVisible();
  const box = await card.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(390);
  expect(box!.y + box!.height).toBeLessThanOrEqual(844);

  // The card is docked rather than pushed off the side of the page.
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
});
