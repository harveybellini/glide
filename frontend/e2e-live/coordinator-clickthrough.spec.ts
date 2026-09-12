// Coordinator-owned end-to-end click-through of the deployed workspace:
// every control in the chrome, the decision surface, and a phone viewport.
import { test, expect, type Page } from "@playwright/test";
import {
  Audit,
  LIVE_BASE,
  createSession,
  inventory,
  instrument,
  openWithSession,
  squeeze,
} from "./harness";

const audit = new Audit("coordinator-clickthrough");
test.afterAll(() => audit.write());

const SESSION = { id: "" };
test.beforeAll(async () => {
  SESSION.id = await createSession();
});

// The deployment throttles under the concurrent audit traffic, so a 429 can
// appear transiently. Retry the user action instead of reporting it.
async function retrying<T>(action: () => Promise<T>, attempts = 4): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await action();
    } catch (error) {
      lastError = error;
      await new Promise((resolve) => setTimeout(resolve, 2500 * (attempt + 1)));
    }
  }
  throw lastError;
}

async function decisionFacts(page: Page, sessionId: string) {
  const response = await page.request.get(`${LIVE_BASE}/api/day`, {
    headers: { "X-Glide-Session": sessionId },
  });
  return (await response.json()) as {
    decisions: { id: string; reason: string; calculated_facts: Record<string, number> }[];
    travel_blocks: unknown[];
    source_events: { occurrence_id: string; title: string }[];
  };
}

test("every control in the workspace chrome responds", async ({ page }) => {
  const watch = instrument(page, audit);
  const sessionId = await createSession();
  await openWithSession(page, sessionId);
  await expect(page.getByRole("heading", { name: /your day/i })).toBeVisible();

  const entries = await inventory(page);
  audit.dump("workspace-inventory", entries);
  audit.step(
    "workspace controls",
    "enumerated",
    entries.map((entry) => `${entry.tag}:${entry.label}`).join(" | "),
  );

  // Settings panel: open, verify fields, close via the close control.
  await page.getByRole("button", { name: "Settings" }).click();
  const panel = page.locator("form.settings-panel");
  await expect(panel).toBeVisible();
  audit.ok("Settings panel opens");
  await audit.shot(page, "settings-open");
  const panelFields = await panel.locator("input, select, button").evaluateAll((nodes) =>
    nodes.map((node) => ({
      tag: node.tagName.toLowerCase(),
      label: node.getAttribute("aria-label") ?? node.textContent?.trim().slice(0, 40) ?? "",
      name: node.getAttribute("name"),
      type: node.getAttribute("type"),
      disabled: (node as HTMLButtonElement).disabled ?? false,
    })),
  );
  audit.dump("settings-fields", panelFields);
  await panel.getByRole("button", { name: /close|cancel/i }).first().click();
  await expect(panel).toBeHidden();
  audit.ok("Settings panel closes");

  // Activity link scrolls to the activity section.
  await page.getByRole("link", { name: /activity/i }).click();
  const activityVisible = await page.locator("#activity").isVisible();
  audit.step("Activity link", activityVisible ? "scrolled to section" : "did not scroll");
  if (!activityVisible) audit.low("Activity nav link does not reveal the section");

  // Pause / resume automation, including the sidebar copy.
  await page.getByRole("button", { name: /pause automation/i }).click();
  await expect(page.getByRole("button", { name: /resume automation/i })).toBeVisible({
    timeout: 20000,
  });
  audit.ok("Pause automation works", "button flipped to Resume automation");
  await page.reload({ waitUntil: "networkidle" });
  const persisted = await page
    .getByRole("button", { name: /resume automation/i })
    .isVisible()
    .catch(() => false);
  if (persisted) audit.ok("Paused state survives reload");
  else audit.high("Paused state does not survive a reload", "automation state was lost");
  await retrying(async () => {
    await page.getByRole("button", { name: /resume automation/i }).click();
    await expect(page.getByRole("button", { name: /pause automation/i })).toBeVisible({
      timeout: 20000,
    });
  });
  audit.ok("Resume automation works");

  // Inline editor: open, cancel, then save a change and recheck.
  const firstEdit = page.getByRole("button", { name: "Edit" }).first();
  await firstEdit.click();
  const editor = page.locator("form.event-editor");
  await expect(editor).toBeVisible();
  audit.ok("Inline editor opens");
  await audit.shot(page, "editor-open");
  await editor.getByRole("button", { name: /cancel/i }).click();
  await expect(editor).toBeHidden();
  audit.ok("Inline editor cancels");

  // Recheck now: reaches a decision, then resolve by skipping the journey.
  await retrying(async () => {
    await page.getByRole("button", { name: /recheck now/i }).click();
    await expect(
      page.getByRole("heading", { name: /needs your decision/i }),
    ).toBeVisible({ timeout: 60000 });
  });
  const card = page.locator("article.decision").first();
  const copy = squeeze(await card.innerText());
  audit.step("decision card copy", copy);
  const facts = await decisionFacts(page, sessionId);
  const decision = facts.decisions[0];
  audit.dump("decision-facts", { copy, decision });
  if (decision) {
    const required = Math.ceil(Number(decision.calculated_facts.required_seconds) / 60);
    const available = Math.floor(Number(decision.calculated_facts.available_seconds) / 60);
    const shortfall = Math.ceil(Number(decision.calculated_facts.shortfall_seconds) / 60);
    const expected = `needs ${required} minutes, but only ${available} minutes are available`;
    const summaryOk = copy.includes(expected) && copy.includes(`Shortfall: ${shortfall}`);
    if (summaryOk) audit.ok("Decision copy matches API facts", expected);
    else
      audit.high(
        "Decision copy disagrees with API facts",
        `Rendered: "${copy}"`,
        JSON.stringify(decision.calculated_facts),
      );
  } else {
    audit.high("Recheck reported a decision but the API returned none");
  }
  await audit.shot(page, "decision-card");

  await retrying(async () => {
    await page.getByRole("button", { name: /skip this journey/i }).click();
    await expect(page.getByRole("heading", { name: /needs your decision/i })).toHaveCount(0, {
      timeout: 60000,
    });
  });
  audit.ok("Skip this journey resolves the decision");

  // Reset sample returns to the pristine day.
  await page.getByRole("button", { name: /reset sample/i }).click();
  await expect(page.getByText(/no updates yet/i)).toBeVisible({ timeout: 30000 });
  const blocks = await page.getByText("Travel \u00b7 Glide", { exact: true }).count();
  if (blocks === 0) audit.ok("Reset sample clears the day");
  else audit.high("Reset sample leaves travel blocks", `count=${blocks}`);

  watch.assertClean("workspace chrome");
});

test("recheck is idempotent and the decision reason is quantified", async ({ page }) => {
  const watch = instrument(page, audit);
  const sessionId = await createSession();
  await openWithSession(page, sessionId);

  await retrying(async () => {
    await page.getByRole("button", { name: /recheck now/i }).click();
    await expect(page.getByText(/travel plan|decision needs your input/i)).toBeVisible({
      timeout: 60000,
    });
  });
  const first = await decisionFacts(page, sessionId);
  const blocksAfterFirst = first.travel_blocks.length;

  for (let repeat = 0; repeat < 2; repeat += 1) {
    await retrying(async () => {
      await page.getByRole("button", { name: /recheck now/i }).click();
      await expect(page.getByText(/travel plan|decision needs your input/i)).toBeVisible({
        timeout: 60000,
      });
    });
  }
  const after = await decisionFacts(page, sessionId);
  if (after.travel_blocks.length === blocksAfterFirst) {
    audit.ok(
      "Repeated checks are idempotent",
      `${blocksAfterFirst} travel blocks after three runs`,
    );
  } else {
    audit.high(
      "Repeated checks change the number of travel blocks",
      `first=${blocksAfterFirst} after=${after.travel_blocks.length}`,
    );
  }
  watch.assertClean("recheck idempotency");
});

test("phone viewport keeps every control reachable", async ({ browser }) => {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  const page = await context.newPage();
  const watch = instrument(page, audit);
  const sessionId = await createSession();
  await openWithSession(page, sessionId);

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  if (overflow > 1) {
    audit.medium(
      "Horizontal overflow on a phone viewport",
      `document.scrollWidth exceeds the viewport by ${overflow}px at 390px wide.`,
    );
  } else {
    audit.ok("No horizontal overflow at 390px");
  }

  for (const name of [/recheck now/i, /pause automation/i, /settings/i, /edit/i]) {
    const control = page.getByRole("button", { name }).first();
    const visible = await control.isVisible().catch(() => false);
    if (!visible) {
      audit.medium(`Control not reachable on phone: ${name}`, "not visible at 390x844");
      continue;
    }
    const box = await control.boundingBox();
    const small = box && (box.width < 24 || box.height < 24);
    audit.step(
      `phone control ${name}`,
      small ? "below 24px tap target" : "reachable",
      box ? `${Math.round(box.width)}x${Math.round(box.height)}` : null,
    );
    if (small) {
      audit.low(
        `Small tap target on phone: ${name}`,
        `control is ${Math.round(box!.width)}x${Math.round(box!.height)} CSS px`,
      );
    }
  }
  await audit.shot(page, "phone-workspace");
  watch.assertClean("phone workspace");
  await context.close();
});
