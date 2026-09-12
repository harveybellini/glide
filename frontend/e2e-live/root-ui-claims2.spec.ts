import { expect, test } from "@playwright/test";

// Follow-up: the first Tab stop and the landing anchor geometry, measured
// after the app has actually mounted.
test("skip link and anchor geometry", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".skip-link")).toBeAttached();
  await page.keyboard.press("Tab");
  console.log(
    "EVIDENCE FIRST_TAB",
    JSON.stringify({
      className: await page.evaluate(() => document.activeElement?.className ?? null),
      text: await page.evaluate(() => document.activeElement?.textContent ?? null),
    }),
  );
  await page.keyboard.press("Enter");
  await page.waitForTimeout(500);
  console.log(
    "EVIDENCE AFTER_ENTER",
    JSON.stringify({
      hash: await page.evaluate(() => location.hash),
      activeId: await page.evaluate(() => document.activeElement?.id ?? null),
    }),
  );

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("link", { name: /how it works/i }).click();
  await page.waitForTimeout(1500);
  console.log(
    "EVIDENCE ANCHOR_GEOMETRY",
    JSON.stringify(
      await page.evaluate(() => {
        const section = document.getElementById("how-it-works");
        const rect = section?.getBoundingClientRect();
        return {
          hash: location.hash,
          scrollY: Math.round(window.scrollY),
          innerHeight: window.innerHeight,
          scrollHeight: document.documentElement.scrollHeight,
          atBottom:
            Math.round(window.scrollY + window.innerHeight) >=
            document.documentElement.scrollHeight - 2,
          sectionTop: rect ? Math.round(rect.top) : null,
          sectionVisible: rect ? rect.top < window.innerHeight && rect.bottom > 0 : null,
        };
      }),
    ),
  );
});
