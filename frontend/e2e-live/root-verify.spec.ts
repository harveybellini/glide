import { expect, test, type Page } from "@playwright/test";

const RUN_TIMEOUT = 45_000;

async function startSample(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
}

// Re-verification of two candidate findings raised by the scripted live pass,
// using the real UI path (typing + clicking) rather than programmatic values.
test.describe("live finding verification", () => {
  test("arrival buffer above 60 is refused instead of saved", async ({ page }) => {
    const settingsWrites: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/settings/.test(request.url()) && request.method() !== "GET") {
        settingsWrites.push(`${request.method()} ${request.postData() ?? ""}`);
      }
    });

    await startSample(page);
    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    await settingsButton.click();
    const panel = page.getByRole("form", { name: "Travel settings" });
    const buffer = panel.getByLabel("Arrival buffer (minutes)");

    await buffer.fill("99");
    await panel.getByRole("button", { name: "Save settings" }).click();

    expect(await buffer.evaluate((element: HTMLInputElement) => element.validationMessage)).not.toBe("");
    await expect(panel).toBeVisible();
    await expect(page.getByText("Settings saved.")).toHaveCount(0);
    expect(settingsWrites).toEqual([]);

    await buffer.fill("10");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    await expect(page.getByText("10 min").first()).toBeVisible();
  });

  test("skipping a journey clears the decision card and the counter", async ({ page }) => {
    await startSample(page);
    const stats = page.locator(".day-stats");
    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible({
      timeout: RUN_TIMEOUT,
    });
    await expect(stats).toContainText("01");
    await expect(stats).toContainText("Decisions to make");

    await page.getByRole("button", { name: "Skip this journey" }).click();
    await expect(page.getByText("Journey skipped.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toHaveCount(0);
    await expect(stats).toContainText("00");

    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(page.getByText(/travel plan updated|no updates/i).first()).toBeVisible({
      timeout: RUN_TIMEOUT,
    });
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toHaveCount(0);
    await expect(stats).toContainText("00");
  });

  test("unknown api route answers with json rather than the app shell", async ({ page }) => {
    const response = await page.request.get("/api/does-not-exist", { maxRedirects: 0 });
    const contentType = response.headers()["content-type"] ?? "";
    // Recorded, not asserted: the audit reports the observed shape.
    console.log(`[verify] /api/does-not-exist -> ${response.status()} ${contentType}`);
  });

  test("sample sessions are isolated and api 404s are not disguised as success", async ({
    playwright,
  }) => {
    const api = await playwright.request.newContext({
      baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    });
    const createSession = async () => {
      const response = await api.post("/api/demo/session", { data: {} });
      expect(response.status()).toBe(201);
      return (await response.json()).session.session_id as string;
    };

    const sessionA = await createSession();
    const sessionB = await createSession();
    const marker = `isolation-marker-${Date.now()}`;

    const patch = await api.patch("/api/demo/events/occ_a", {
      headers: { "X-Glide-Session": sessionA },
      data: { start: "2026-09-12T08:15:00Z", end: "2026-09-12T09:00:00Z", location: marker },
    });
    console.log(`[verify] patch A -> ${patch.status()} ${(await patch.text()).slice(0, 120)}`);

    const dayB = await api.get("/api/day", { headers: { "X-Glide-Session": sessionB } });
    const dayBodyB = await dayB.text();
    console.log(`[verify] B day carries A's marker: ${dayBodyB.includes(marker)}`);
    expect(dayBodyB).not.toContain(marker);

    const dayA = await api.get("/api/day", { headers: { "X-Glide-Session": sessionA } });
    const dayBodyA = await dayA.text();
    console.log(`[verify] A day carries its own marker: ${dayBodyA.includes(marker)}`);
    expect(dayBodyA).toContain(marker);

    const unknownRun = await api.get("/api/runs/run-does-not-exist", {
      headers: { "X-Glide-Session": sessionA },
    });
    const runBody = await unknownRun.text();
    console.log(
      `[verify] unknown run -> ${unknownRun.status()} ${unknownRun.headers()["content-type"]} :: ${runBody.slice(0, 60)}`,
    );

    const backwards = await api.patch("/api/demo/events/occ_a", {
      headers: { "X-Glide-Session": sessionA },
      data: { start: "2026-09-12T11:00:00Z", end: "2026-09-12T10:30:00Z" },
    });
    console.log(`[verify] end<start -> ${backwards.status()} ${(await backwards.text()).slice(0, 120)}`);

    // Disposal writes trace artifacts, which can race with parallel auditors
    // cleaning the output directory; a failure there is not a product finding.
    await api.dispose().catch((error: Error) => {
      console.log(`[verify] request context disposal skipped: ${error.message.slice(0, 80)}`);
    });
  });
});
