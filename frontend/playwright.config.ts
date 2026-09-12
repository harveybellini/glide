import { defineConfig } from "@playwright/test";

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173";

/**
 * Every spec that is not about the guided tour starts with it already seen.
 *
 * Playwright hands each test a fresh browser profile, so without this the tour
 * would open on top of every spec and make unrelated checks depend on it.
 * `e2e/tour.spec.ts` opts back out with an empty storage state and drives the
 * first-visit flow deliberately; `?tour=1` forces the tour open too.
 */
const tourAlreadySeen = {
  cookies: [],
  origins: [
    {
      origin: new URL(baseURL).origin,
      localStorage: [{ name: "glide-tour-v1", value: "done" }],
    },
  ],
};

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    storageState: tourAlreadySeen,
    viewport: { width: 1200, height: 800 },
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        browserName: "chromium",
        viewport: { width: 1200, height: 800 },
      },
    },
  ],
});
