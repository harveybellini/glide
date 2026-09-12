import { expect, test, type Page } from "@playwright/test";

// Root UI verification: time zone handling, clearing the earliest departure,
// anchor scrolling on the landing page, and the seconds shown after a save.

const RUN_TIMEOUT = 60_000;

async function startSample(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
}

test.describe("root ui claims", () => {
  test("changing the time zone does not change the displayed day", async ({ page }) => {
    await startSample(page);
    const before = await page.locator("#timeline").innerText();
    const labelBefore = await page.getByText(/times in/i).first().innerText();

    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    await panel.getByLabel("Time zone").selectOption("Europe/Paris");
    const saved = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" && response.url().includes("/api/settings"),
    );
    await panel.getByRole("button", { name: "Save settings" }).click();
    console.log("EVIDENCE TZ_PATCH", (await saved).status());
    await expect(panel).toHaveCount(0);

    const after = await page.locator("#timeline").innerText();
    const labelAfter = await page.getByText(/times in/i).first().innerText();
    const me = await page.evaluate(async () =>
      (await (await fetch("/api/me", { headers: { "X-Glide-Session": localStorage.getItem("glide-sample-session") ?? "" } })).json()),
    );
    console.log(
      "EVIDENCE TIME_ZONE",
      JSON.stringify({
        settingsTimeZone: (me as { time_zone?: string }).time_zone,
        labelBefore,
        labelAfter,
        timelineUnchanged: before === after,
        beforeFirstLines: before.split("\n").slice(0, 6),
        afterFirstLines: after.split("\n").slice(0, 6),
      }),
    );
  });

  test("earliest departure cannot be cleared once set", async ({ page }) => {
    await startSample(page);
    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    const panel = page.getByRole("form", { name: "Travel settings" });

    await settingsButton.click();
    await panel.getByLabel("Earliest departure (optional)").fill("07:45");
    const firstSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" && response.url().includes("/api/settings"),
    );
    await panel.getByRole("button", { name: "Save settings" }).click();
    const firstPatch = await firstSave;
    await expect(panel).toHaveCount(0);
    console.log(
      "EVIDENCE DEPARTURE_SET",
      JSON.stringify({ status: firstPatch.status(), body: firstPatch.request().postData() }),
    );

    // Now clear it, exactly as a user would.
    await expect(settingsButton).toBeEnabled();
    await settingsButton.click();
    await expect(panel).toBeVisible();
    console.log(
      "EVIDENCE DEPARTURE_ON_REOPEN",
      JSON.stringify(await panel.getByLabel("Earliest departure (optional)").inputValue()),
    );
    await panel.getByLabel("Earliest departure (optional)").fill("");
    const clearSave = page.waitForResponse(
      (response) =>
        response.request().method() === "PATCH" && response.url().includes("/api/settings"),
    );
    await panel.getByRole("button", { name: "Save settings" }).click();
    const clearPatch = await clearSave;
    await expect(panel).toHaveCount(0);
    console.log(
      "EVIDENCE DEPARTURE_CLEAR_PATCH",
      JSON.stringify({ status: clearPatch.status(), body: clearPatch.request().postData() }),
    );

    await expect(settingsButton).toBeEnabled();
    await settingsButton.click();
    await expect(panel).toBeVisible();
    const afterClear = await panel.getByLabel("Earliest departure (optional)").inputValue();
    console.log("EVIDENCE DEPARTURE_AFTER_CLEAR", JSON.stringify(afterClear));
    await panel.screenshot({
      path: "../temp/live-audit/artifacts/root-evidence/departure-after-clear.png",
    });
  });

  test('landing anchors land on their sections', async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.getByRole("link", { name: /how it works/i }).click();
    await page.waitForTimeout(1200);
    const top = await page.evaluate(() =>
      Math.round(document.getElementById("how-it-works")?.getBoundingClientRect().top ?? -1),
    );
    const scrolled = await page.evaluate(() => Math.round(window.scrollY));
    console.log(
      "EVIDENCE HOW_IT_WORKS",
      JSON.stringify({ sectionTop: top, scrollY: scrolled, hash: await page.evaluate(() => location.hash) }),
    );

    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.className ?? null);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(600);
    console.log(
      "EVIDENCE SKIP_LINK",
      JSON.stringify({
        firstTabStop: focused,
        hashAfterEnter: await page.evaluate(() => location.hash),
        activeElementId: await page.evaluate(() => document.activeElement?.id ?? null),
      }),
    );
  });
});
