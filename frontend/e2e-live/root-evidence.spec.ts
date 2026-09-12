import { expect, test, type Page } from "@playwright/test";

// Root evidence pass: byte-exact capture of rendered copy plus the settings
// edge cases found by root-full-controls.spec.ts. Own file on purpose - other
// agents edit frontend/e2e/root-verify.spec.ts. Run with --trace=off.

const RUN_TIMEOUT = 60_000;

function codes(text: string) {
  return Array.from(text)
    .map((char) => `U+${char.codePointAt(0)!.toString(16).toUpperCase().padStart(4, "0")}`)
    .join(" ");
}

async function startSample(page: Page) {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByRole("button", { name: /try a sample day/i }).click();
  await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
}

test.describe("root evidence", () => {
  test("settings round trip, validation, and clearing earliest departure", async ({
    page,
  }) => {
    const patches: { body: string | null; status: number }[] = [];
    page.on("response", (response) => {
      if (
        response.request().method() === "PATCH" &&
        response.url().includes("/api/settings")
      ) {
        patches.push({ body: response.request().postData(), status: response.status() });
      }
    });

    await startSample(page);
    const settingsButton = page.getByRole("button", { name: "Settings", exact: true });
    const panel = page.getByRole("form", { name: "Travel settings" });
    const openSettings = async () => {
      await expect(settingsButton).toBeEnabled();
      await settingsButton.click();
      await expect(panel).toBeVisible();
    };

    await openSettings();
    await panel.getByLabel("Arrival buffer (minutes)").fill("15");
    await panel.getByLabel("Earliest departure (optional)").fill("07:45");
    await panel.getByLabel("Start address").selectOption("place_b");
    await panel.getByLabel("Time zone").selectOption("Europe/Paris");
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    console.log("EVIDENCE PATCH_1", JSON.stringify(patches));

    await openSettings();
    console.log(
      "EVIDENCE ROUND_TRIP",
      JSON.stringify({
        buffer: await panel.getByLabel("Arrival buffer (minutes)").inputValue(),
        departure: await panel.getByLabel("Earliest departure (optional)").inputValue(),
        start: await panel.getByLabel("Start address").inputValue(),
        zone: await panel.getByLabel("Time zone").inputValue(),
      }),
    );

    // Out-of-range buffer: the browser blocks the submit; nothing is sent.
    const before = patches.length;
    await panel.getByLabel("Arrival buffer (minutes)").fill("61");
    await panel.getByRole("button", { name: "Save settings" }).click();
    console.log(
      "EVIDENCE BUFFER_61",
      JSON.stringify({
        validationMessage: await panel
          .getByLabel("Arrival buffer (minutes)")
          .evaluate((element: HTMLInputElement) => element.validationMessage),
        panelStillOpen: await panel.isVisible(),
        patchesSent: patches.length - before,
      }),
    );

    // Clearing the earliest departure is offered by the UI. Does it stick?
    await panel.getByLabel("Arrival buffer (minutes)").fill("15");
    await panel.getByLabel("Earliest departure (optional)").fill("");
    const beforeClear = patches.length;
    await panel.getByRole("button", { name: "Save settings" }).click();
    await expect(page.getByText("Settings saved.")).toBeVisible();
    console.log(
      "EVIDENCE CLEAR_DEPARTURE",
      JSON.stringify({
        patchSent: patches.slice(beforeClear).map((entry) => entry.body),
      }),
    );
    await openSettings();
    console.log(
      "EVIDENCE DEPARTURE_AFTER_CLEAR",
      JSON.stringify(await panel.getByLabel("Earliest departure (optional)").inputValue()),
    );

    await panel.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(panel).toHaveCount(0);
  });

  test("rendered copy is byte-exact across the landing page and the day view", async ({
    page,
  }) => {
    const markers = ["\u00C2", "\u00C3", "\u00E2\u0080", "\u00E2\u0082"];

    await page.goto("/", { waitUntil: "domcontentloaded" });
    const landing = await page.locator("body").innerText();
    console.log("EVIDENCE LANDING_TITLE", JSON.stringify(await page.title()));
    console.log("EVIDENCE LANDING_TEXT", JSON.stringify(landing));
    console.log(
      "EVIDENCE LANDING_MARKERS",
      JSON.stringify(markers.filter((marker) => landing.includes(marker))),
    );

    await page.getByRole("button", { name: /try a sample day/i }).click();
    await expect(page.getByRole("region", { name: "Calendar timeline" })).toBeVisible();
    const workspace = await page.locator("body").innerText();
    console.log("EVIDENCE WORKSPACE_TEXT", JSON.stringify(workspace));
    console.log(
      "EVIDENCE WORKSPACE_MARKERS",
      JSON.stringify(markers.filter((marker) => workspace.includes(marker))),
    );

    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible({
      timeout: RUN_TIMEOUT,
    });
    const travelRows = page.locator("article.row.travel");
    await expect(travelRows).toHaveCount(1);
    const travel = await travelRows.first().innerText();
    console.log("EVIDENCE TRAVEL_TEXT", JSON.stringify(travel));
    console.log("EVIDENCE TRAVEL_CODES", codes(travel));
    console.log(
      "EVIDENCE TRAVEL_MARKERS",
      JSON.stringify(markers.filter((marker) => travel.includes(marker))),
    );

    const heading = await page.getByRole("heading", { name: "Needs your decision" }).innerText();
    console.log("EVIDENCE DECISION_HEADING_CODES", codes(heading));
    const decision = await page.locator("article.decision").first().innerText();
    console.log("EVIDENCE DECISION_TEXT", JSON.stringify(decision));
    const body = await page.locator("body").innerText();
    await page.screenshot({
      path: "../temp/live-audit/artifacts/root-evidence/day-with-decision.png",
      fullPage: true,
    });
    console.log("EVIDENCE BODY_LENGTH", String(body.length));
  });

  test("reload keeps the plan and reset clears generated state", async ({ page }) => {
    await startSample(page);
    await page.getByRole("button", { name: "Recheck now" }).click();
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible({
      timeout: RUN_TIMEOUT,
    });
    const travelRows = page.locator("article.row.travel");
    await expect(travelRows).toHaveCount(1);
    console.log("EVIDENCE TRAVEL_ROW_TEXT", JSON.stringify(await travelRows.first().innerText()));

    await page.reload({ waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toBeVisible();
    await expect(travelRows).toHaveCount(1);
    console.log("EVIDENCE RELOAD_OK", "decision and travel row survived reload");

    await page.getByRole("button", { name: "Reset sample", exact: true }).click();
    await expect(page.getByText("Sample reset to its starting state.")).toBeVisible();
    await expect(page.getByText("No updates yet. Run a check to see changes here.")).toBeVisible();
    await expect(travelRows).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Needs your decision" })).toHaveCount(0);
    console.log("EVIDENCE RESET_OK", "reset removed the decision and the travel row");
  });
});
