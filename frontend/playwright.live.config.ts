import { defineConfig } from "@playwright/test";

// Live-site audit config. Runs the same Playwright runner against the deployed
// CloudFront URL so every probe has real network, real CloudFront caching and
// the real API behind it. Kept separate from playwright.config.ts so the
// default suite keeps running against localhost.
// Each concurrent audit run gets its own output directory: several agents run
// this config at the same time, and Playwright wipes its outputDir on start.
const area = process.env.GLIDE_AUDIT_AREA ?? `run-${process.pid}`;

export default defineConfig({
  testDir: "./e2e-live",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: `../temp/live-audit/test-results/${area}`,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "https://d3tvxy281s2u11.cloudfront.net",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    video: "off",
    ignoreHTTPSErrors: false,
  },
  projects: [
    {
      name: "live-chromium",
      use: { browserName: "chromium", viewport: { width: 1440, height: 900 } },
    },
  ],
});
