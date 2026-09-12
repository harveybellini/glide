import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const BASE = "https://d3tvxy281s2u11.cloudfront.net";

// Copy that exists in the current source tree, used to detect a stale deploy.
const MARKERS: Array<[string, string]> = [
  ["tagline", "Room for the journey."],
  ["hero", "Life happens"],
  ["sample_cta", "No account needed. Just a little curiosity."],
  ["skip_landing", "Skip to get started"],
  ["skip_workspace", "Skip to timeline"],
  ["buffer_label", "Arrival buffer (minutes)"],
  ["decision_emails", "Decision emails"],
  ["reconnect", "Reconnect Google Calendar"],
  ["edit_in_google", "Edit in Google Calendar"],
  ["no_fixed_start", "No fixed start (ask me)"],
  ["footer_live", "Your appointments stay yours."],
  ["footer_sample", "Fictional events · Simulated routes"],
  ["how_it_works", "A calendar that connects the dots."],
  ["travel_block", "Travel · Glide"],
];

test("deployed bundle freshness and asset headers", async ({ page, request }) => {
  const assets: string[] = [];
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/assets/")) assets.push(`${response.status()} ${response.headers()["cache-control"] ?? "no-cache-control"} ${url}`);
  });
  const response = await page.goto(BASE, { waitUntil: "networkidle" });
  const headers = response?.headers() ?? {};
  console.log("HTML_STATUS", response?.status());
  console.log("HTML_HEADERS", JSON.stringify(headers, null, 1));
  console.log("ASSETS", JSON.stringify(assets, null, 1));

  const scriptSrcs = await page.locator("script[src]").evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLScriptElement).src),
  );
  const styles = await page.locator('link[rel="stylesheet"]').evaluateAll((nodes) =>
    nodes.map((node) => (node as HTMLLinkElement).href),
  );
  console.log("SCRIPTS", JSON.stringify(scriptSrcs));
  console.log("STYLES", JSON.stringify(styles));
  const meta = await page.evaluate(() => ({
    title: document.title,
    description: document.querySelector('meta[name="description"]')?.getAttribute("content") ?? null,
    ogTitle: document.querySelector('meta[property="og:title"]')?.getAttribute("content") ?? null,
    ogImage: document.querySelector('meta[property="og:image"]')?.getAttribute("content") ?? null,
    favicon: document.querySelector('link[rel="icon"]')?.getAttribute("href") ?? null,
    viewport: document.querySelector('meta[name="viewport"]')?.getAttribute("content") ?? null,
    lang: document.documentElement.lang,
    themeColor: document.querySelector('meta[name="theme-color"]')?.getAttribute("content") ?? null,
  }));
  console.log("META", JSON.stringify(meta));

  const bundleText: string[] = [];
  for (const src of scriptSrcs) {
    const got = await request.get(src);
    console.log("BUNDLE", src, got.status(), got.headers()["content-encoding"] ?? "identity", got.headers()["cache-control"] ?? "-");
    if (got.ok()) bundleText.push(await got.text());
  }
  const stylesText: string[] = [];
  for (const href of styles) {
    const got = await request.get(href);
    console.log("STYLE", href, got.status(), got.headers()["cache-control"] ?? "-");
    if (got.ok()) stylesText.push(await got.text());
  }
  const haystack = bundleText.join("\n");
  const cssHaystack = stylesText.join("\n");
  for (const [name, marker] of MARKERS) {
    console.log(`MARKER ${name}: ${haystack.includes(marker) ? "present" : "MISSING"} (${marker})`);
  }
  const cssMarkers = [".app", ".landing", ".day-preview", ".decision-focused", ".skip-link"];
  for (const marker of cssMarkers) {
    console.log(`CSS ${marker}: ${cssHaystack.includes(marker) ? "present" : "MISSING"}`);
  }
  console.log("BUNDLE_BYTES", haystack.length, "CSS_BYTES", cssHaystack.length);
});

test("not-found path and decision deep link behaviour", async ({ page }) => {
  const notFound = await page.goto(`${BASE}/this-does-not-exist`, { waitUntil: "domcontentloaded" });
  console.log("NOT_FOUND_STATUS", notFound?.status());
  console.log("NOT_FOUND_TITLE", await page.title());
  console.log("NOT_FOUND_BODY", (await page.locator("body").innerText()).replace(/\s+/g, " ").slice(0, 200));

  await page.goto(`${BASE}/?decision=bogus-decision-id`, { waitUntil: "domcontentloaded" });
  await expect(page.getByRole("button", { name: /try a sample day/i })).toBeVisible();
  console.log("BOGUS_DECISION_OK", true);

  // Real decision deep link on a fresh sample session.
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByText(/sample workspace/i).first()).toBeVisible();
  await page.getByRole("button", { name: /recheck now/i }).click();
  await expect(page.locator('section[aria-label="Needs your decision"]')).toBeVisible({ timeout: 120000 });
  const decisionId = await page
    .locator('section[aria-label="Needs your decision"] article')
    .first()
    .getAttribute("id");
  console.log("DECISION_ID", decisionId);
  const realId = (decisionId ?? "").replace("decision-", "");
  await page.goto(`${BASE}/?decision=${realId}`, { waitUntil: "domcontentloaded" });
  await expect(page.locator(`#decision-${realId}`)).toBeVisible({ timeout: 30000 });
  console.log("HIGHLIGHT_CLASS", await page.locator(`#decision-${realId}`).getAttribute("class"));
  await page.waitForTimeout(1200);
  console.log("URL_AFTER_SCROLL", page.url());

  // SPA fallback for a deep client route is not required, but a stale decision id must not crash.
  await page.goto(`${BASE}/?decision=stale-id-123`, { waitUntil: "domcontentloaded" });
  console.log("STALE_DECISION_OK", await page.getByRole("button", { name: /try a sample day/i }).isVisible());

  const localSource = readFileSync(join(process.cwd(), "src", "App.tsx"), "utf8");
  console.log("LOCAL_APP_TSX_BYTES", localSource.length);
});
