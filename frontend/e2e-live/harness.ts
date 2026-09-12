// Shared instrumentation for the live-site functional audit.
//
// Report: temp/live-audit/reports/<area>.md
// Shots:  temp/live-audit/artifacts/<area>/
//
// Usage in a spec:
//   const audit = new Audit("area-name");
//   test.afterAll(() => audit.write());
import fs from "node:fs";
import path from "node:path";
import { expect, type Page, type Response } from "@playwright/test";

export const LIVE_BASE =
  process.env.PLAYWRIGHT_BASE_URL ?? "https://d3tvxy281s2u11.cloudfront.net";

const AUDIT_ROOT = path.resolve(process.cwd(), "..", "temp", "live-audit");

const SEVERITY_ORDER = ["blocker", "high", "medium", "low", "nit", "pass"];

export interface Finding {
  severity: string;
  title: string;
  detail: string;
  evidence: string | null;
  area: string;
}

export class Audit {
  readonly area: string;
  readonly findings: Finding[] = [];
  readonly steps: { name: string; outcome: string; detail: string | null }[] = [];
  readonly artifacts: string;
  private shots = 0;

  constructor(area: string) {
    this.area = area;
    this.artifacts = path.join(AUDIT_ROOT, "artifacts", area);
    fs.mkdirSync(this.artifacts, { recursive: true });
  }

  private add(severity: string, title: string, detail: string, evidence?: string | null) {
    this.findings.push({
      severity,
      title,
      detail,
      evidence: evidence ?? null,
      area: this.area,
    });
  }

  blocker(title: string, detail: string, evidence?: string | null) {
    this.add("blocker", title, detail, evidence);
  }
  high(title: string, detail: string, evidence?: string | null) {
    this.add("high", title, detail, evidence);
  }
  medium(title: string, detail: string, evidence?: string | null) {
    this.add("medium", title, detail, evidence);
  }
  low(title: string, detail: string, evidence?: string | null) {
    this.add("low", title, detail, evidence);
  }
  nit(title: string, detail: string, evidence?: string | null) {
    this.add("nit", title, detail, evidence);
  }
  ok(title: string, detail = "") {
    this.add("pass", title, detail, null);
  }

  step(name: string, outcome: string, detail?: string | null) {
    this.steps.push({ name, outcome, detail: detail ?? null });
  }

  async shot(page: Page, name: string) {
    this.shots += 1;
    const file = path.join(
      this.artifacts,
      `${String(this.shots).padStart(2, "0")}-${name.replace(/[^a-z0-9-]+/gi, "-")}.png`,
    );
    try {
      await page.screenshot({ path: file, fullPage: true });
      this.step(`screenshot ${name}`, "captured", file);
      return file;
    } catch (error) {
      this.step(`screenshot ${name}`, "failed", String(error));
      return null;
    }
  }

  // Records a JSON blob next to the screenshots for later inspection.
  dump(name: string, value: unknown) {
    const file = path.join(this.artifacts, `${name}.json`);
    fs.writeFileSync(file, JSON.stringify(value, null, 2));
    return file;
  }

  write() {
    const dir = path.join(AUDIT_ROOT, "reports");
    fs.mkdirSync(dir, { recursive: true });
    const counts: Record<string, number> = {};
    for (const finding of this.findings) {
      counts[finding.severity] = (counts[finding.severity] ?? 0) + 1;
    }
    const payload = {
      area: this.area,
      generatedAt: new Date().toISOString(),
      baseUrl: LIVE_BASE,
      counts,
      findings: this.findings,
      steps: this.steps,
    };
    fs.writeFileSync(path.join(dir, `${this.area}.json`), JSON.stringify(payload, null, 2));

    const ordered = [...this.findings].sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    );
    const lines = [
      `# Live audit: ${this.area}`,
      "",
      `Base URL: ${LIVE_BASE}`,
      `Generated: ${payload.generatedAt}`,
      "",
      "## Findings",
      "",
    ];
    if (!ordered.length) lines.push("_No findings recorded._");
    for (const finding of ordered) {
      lines.push(`### [${finding.severity}] ${finding.title}`, "", finding.detail);
      if (finding.evidence) {
        lines.push("", "```", finding.evidence, "```");
      }
      lines.push("");
    }
    lines.push("## Steps exercised", "");
    for (const step of this.steps) {
      lines.push(
        `- ${step.outcome.toUpperCase()} \u2014 ${step.name}${step.detail ? ` :: ${step.detail}` : ""}`,
      );
    }
    lines.push("");
    fs.writeFileSync(path.join(dir, `${this.area}.md`), lines.join("\n"));
    return payload;
  }
}

// Collects console output, uncaught exceptions, failed requests and 4xx/5xx
// responses for one page. Call `watch.assertClean("label")` at the end.
export function instrument(page: Page, audit: Audit) {
  const consoleErrors: string[] = [];
  const consoleWarnings: string[] = [];
  const pageErrors: string[] = [];
  const failedRequests: string[] = [];
  const httpErrors: string[] = [];
  const apiCalls: { method: string; url: string; status: number }[] = [];

  page.on("console", (message) => {
    const text = `${message.text()} @ ${message.location().url}:${message.location().lineNumber}`;
    if (message.type() === "error") consoleErrors.push(text);
    else if (message.type() === "warning") consoleWarnings.push(text);
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("requestfailed", (request) => {
    failedRequests.push(
      `${request.method()} ${request.url()} :: ${request.failure()?.errorText ?? "unknown"}`,
    );
  });
  page.on("response", (response: Response) => {
    const url = response.url();
    if (url.includes("/api/")) {
      apiCalls.push({ method: response.request().method(), url, status: response.status() });
    }
    if (response.status() >= 400) {
      httpErrors.push(`${response.status()} ${response.request().method()} ${url}`);
    }
  });

  return {
    consoleErrors,
    consoleWarnings,
    pageErrors,
    failedRequests,
    httpErrors,
    apiCalls,
    // Unexpected 4xx/5xx. Expected-status helpers below filter by design.
    unexpectedHttpErrors(expected: number[] = []) {
      return httpErrors.filter(
        (entry) => !expected.includes(Number(entry.split(" ")[0])),
      );
    },
    assertClean(label: string) {
      if (pageErrors.length) {
        audit.high(
          `Uncaught page error on ${label}`,
          "A JavaScript exception escaped to the browser console.",
          pageErrors.join("\n"),
        );
      }
      if (consoleErrors.length) {
        audit.medium(
          `Console errors on ${label}`,
          `The browser logged ${consoleErrors.length} console error(s).`,
          consoleErrors.join("\n"),
        );
      }
      if (failedRequests.length) {
        audit.high(
          `Failed network requests on ${label}`,
          "One or more requests did not complete.",
          failedRequests.join("\n"),
        );
      }
      const unexpected = this.unexpectedHttpErrors();
      if (unexpected.length) {
        audit.medium(
          `HTTP 4xx/5xx responses on ${label}`,
          "The page received an error response.",
          unexpected.join("\n"),
        );
      }
    },
  };
}

export interface InventoryEntry {
  index: number;
  tag: string;
  type: string | null;
  role: string | null;
  label: string;
  name: string | null;
  href: string | null;
  disabled: boolean;
  visible: boolean;
  tabbable: boolean;
  box: number[];
}

// Everything a user could interact with on the current screen.
export async function inventory(page: Page): Promise<InventoryEntry[]> {
  return page.evaluate(() => {
    const selector =
      'a, button, input, select, textarea, [role="button"], [role="link"], [tabindex]:not([tabindex="-1"]), summary, details';
    return Array.from(document.querySelectorAll<HTMLElement>(selector))
      .map((node, index) => {
        const rect = node.getBoundingClientRect();
        const style = window.getComputedStyle(node);
        const visible =
          rect.width > 0 &&
          rect.height > 0 &&
          style.visibility !== "hidden" &&
          style.display !== "none";
        const label =
          node.getAttribute("aria-label") ||
          node.textContent?.trim().replace(/\s+/g, " ").slice(0, 80) ||
          node.getAttribute("placeholder") ||
          node.getAttribute("name") ||
          "";
        return {
          index,
          tag: node.tagName.toLowerCase(),
          type: node.getAttribute("type"),
          role: node.getAttribute("role"),
          label,
          name: node.getAttribute("name"),
          href: node.getAttribute("href"),
          disabled:
            node.hasAttribute("disabled") || node.getAttribute("aria-disabled") === "true",
          visible,
          tabbable: node.tabIndex >= 0,
          box: [
            Math.round(rect.x),
            Math.round(rect.y),
            Math.round(rect.width),
            Math.round(rect.height),
          ],
        };
      })
      .filter((entry) => entry.visible);
  });
}

// Creates a demo session straight through the API so a spec can start deep in
// the product without replaying the landing page.
export async function createSession(): Promise<string> {
  const response = await fetch(`${LIVE_BASE}/api/demo/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!response.ok) throw new Error(`session create failed: ${response.status}`);
  const payload = (await response.json()) as { session: { session_id: string } };
  return payload.session.session_id;
}

export const SESSION_KEY = "glide-sample-session";

// Opens the app with a prepared sample session already in localStorage.
export async function openWithSession(page: Page, sessionId: string, path = "/") {
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, sessionId],
  );
  await page.goto(path, { waitUntil: "networkidle" });
}

// Reads a JSON API endpoint with an optional session header.
export async function apiGet<T>(path: string, sessionId?: string): Promise<T> {
  const response = await fetch(`${LIVE_BASE}${path}`, {
    headers: sessionId ? { "X-Glide-Session": sessionId } : {},
  });
  if (!response.ok) throw new Error(`${path} -> ${response.status}`);
  return (await response.json()) as T;
}

export function squeeze(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export { expect };
