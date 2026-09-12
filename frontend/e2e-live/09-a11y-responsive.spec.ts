// Accessibility, keyboard and responsive behaviour of the deployed day view.
import { test } from "@playwright/test";
import {
  Audit,
  LIVE_BASE,
  SESSION_KEY,
  createSession,
  expect,
  instrument,
  inventory,
  openWithSession,
  squeeze,
} from "./harness";

const audit = new Audit("09-a11y-responsive");

test.afterAll(() => {
  audit.write();
});

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "laptop-1024", width: 1024, height: 768 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "phone-390", width: 390, height: 844 },
  { name: "phone-320", width: 320, height: 568 },
];

// The site is under heavy concurrent audit load from sibling agents, so a
// session can occasionally fail to resolve on the first try. Retry the
// session handoff, and record the API status each time so a genuine
// session-loading defect is still visible in the report.
async function enterDayView(page: import("@playwright/test").Page) {
  let lastError: unknown = null;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const sessionId = await createSession();
    await openWithSession(page, sessionId, "/");
    try {
      await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible({
        timeout: 20_000,
      });
      if (attempt > 1) {
        audit.step("session handoff", `succeeded on attempt ${attempt}`);
      }
      return sessionId;
    } catch (error) {
      lastError = error;
      const probe = await page.request.get(`${LIVE_BASE}/api/day`, {
        headers: { "X-Glide-Session": sessionId },
      });
      audit.step(
        `session handoff attempt ${attempt}`,
        String(probe.status()),
        squeeze(await probe.text()).slice(0, 200),
      );
    }
  }
  audit.high(
    "A freshly created sample session did not render the day view",
    "Three session handoffs in a row failed to reach the timeline; the judge would see the landing page instead of the sample day.",
    String(lastError),
  );
  throw lastError;
}

test("responsive matrix: no horizontal overflow, tap targets, readable layout", async ({
  browser,
}) => {
  for (const viewport of VIEWPORTS) {
    const context = await browser.newContext({
      viewport: { width: viewport.width, height: viewport.height },
    });
    const page = await context.newPage();
    const watch = instrument(page, audit);
    await enterDayView(page);

    const metrics = await page.evaluate(() => {
      const root = document.documentElement;
      const wide: { tag: string; label: string; width: number; left: number }[] = [];
      for (const node of Array.from(document.querySelectorAll<HTMLElement>("body *"))) {
        const rect = node.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        if (rect.right > window.innerWidth + 1 || rect.left < -1) {
          wide.push({
            tag: node.tagName.toLowerCase(),
            label: (node.className || "").toString().slice(0, 60),
            width: Math.round(rect.width),
            left: Math.round(rect.left),
          });
        }
      }
      return {
        scrollWidth: root.scrollWidth,
        innerWidth: window.innerWidth,
        overflowing: wide.slice(0, 12),
        bodyText: document.body.innerText.slice(0, 120),
      };
    });
    audit.step(
      `viewport ${viewport.name}`,
      `${metrics.scrollWidth}/${metrics.innerWidth}`,
      JSON.stringify(metrics.overflowing),
    );
    if (metrics.scrollWidth > metrics.innerWidth + 1) {
      audit.high(
        `Horizontal overflow at ${viewport.width}px`,
        `documentElement.scrollWidth=${metrics.scrollWidth} exceeds the ${metrics.innerWidth}px viewport.`,
        JSON.stringify(metrics.overflowing),
      );
    }

    const controls = await inventory(page);
    audit.dump(`inventory-${viewport.name}`, controls);
    const small = controls
      .filter((entry) => entry.visible)
      .map((entry) => ({
        label: entry.label,
        w: entry.box[2],
        h: entry.box[3],
      }))
      .filter((entry) => entry.w > 0 && entry.h > 0 && (entry.w < 24 || entry.h < 24));
    if (small.length) {
      audit.step(`tap targets under 24px at ${viewport.name}`, String(small.length), JSON.stringify(small.slice(0, 10)));
    }

    const primary = page.getByRole("button", { name: /Recheck now/i });
    const visible = await primary.isVisible();
    const box = await primary.boundingBox();
    if (!visible || !box || box.width < 24 || box.height < 24) {
      audit.high(
        `Primary action not properly reachable at ${viewport.name}`,
        `Recheck now visible=${visible} box=${JSON.stringify(box)}`,
      );
    }
    await audit.shot(page, viewport.name);
    watch.assertClean(`viewport ${viewport.name}`);
    await context.close();
  }
});

test("keyboard: skip link first, visible focus, every control reachable", async ({
  browser,
}) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await enterDayView(page);

  const stops: { index: number; tag: string; label: string; focusVisible: boolean }[] = [];
  for (let index = 0; index < 40; index += 1) {
    await page.keyboard.press("Tab");
    const stop = await page.evaluate(() => {
      const node = document.activeElement as HTMLElement | null;
      if (!node || node === document.body) return null;
      const style = window.getComputedStyle(node);
      const outline = `${style.outlineStyle} ${style.outlineWidth} ${style.outlineColor}`;
      const focusVisible =
        (style.outlineStyle !== "none" && style.outlineWidth !== "0px") ||
        style.boxShadow !== "none";
      return {
        tag: node.tagName.toLowerCase(),
        label:
          node.getAttribute("aria-label") ||
          (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60),
        focusVisible,
        outline,
      };
    });
    if (!stop) break;
    stops.push({ index, ...stop });
  }
  audit.dump("tab-order", stops);
  audit.step("tab stops", String(stops.length), JSON.stringify(stops.slice(0, 6)));

  const first = stops[0];
  if (!first?.label.toLowerCase().includes("skip")) {
    audit.medium(
      "Skip link is not the first tab stop",
      `First stop was ${JSON.stringify(first)}`,
    );
  }
  const invisible = stops.filter((stop) => !stop.focusVisible);
  if (invisible.length) {
    audit.medium(
      "Focus indicator missing on at least one tab stop",
      `${invisible.length}/${stops.length} stops had no outline or shadow.`,
      JSON.stringify(invisible.slice(0, 8)),
    );
  } else {
    audit.ok("Every sampled tab stop had a visible focus indicator", `${stops.length} stops`);
  }

  const controls = (await inventory(page)).filter(
    (entry) => entry.visible && entry.tabbable && !entry.disabled,
  );
  const seen = new Set(stops.map((stop) => stop.label.toLowerCase()));
  const unreachable = controls.filter(
    (entry) => entry.label && !seen.has(entry.label.toLowerCase()),
  );
  if (unreachable.length) {
    audit.medium(
      "Interactive controls not reached within 40 Tab presses",
      `${unreachable.length} controls were never focused.`,
      JSON.stringify(unreachable.slice(0, 8).map((entry) => entry.label)),
    );
  }

  // Skip link behaviour.
  await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  const afterSkip = await page.evaluate(() => ({
    hash: window.location.hash,
    active: (document.activeElement as HTMLElement | null)?.id ?? null,
    timelineTop: document.getElementById("timeline")?.getBoundingClientRect().top ?? null,
  }));
  audit.step("skip link", "activated", JSON.stringify(afterSkip));
  if (afterSkip.hash !== "#timeline") {
    audit.low(
      "Skip link does not move the URL to the timeline",
      `hash=${afterSkip.hash}, active=${afterSkip.active}`,
    );
  }

  // Anchor navigation.
  for (const [link, target] of [
    [/Activity/i, "#activity"],
    [/My day/i, "#timeline"],
  ] as const) {
    await page.getByRole("link", { name: link }).first().click();
    // Anchor navigation is animated (scroll-behavior: smooth), so wait for the
    // scroll position to settle before measuring.
    let previousTop = Number.NaN;
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await page.waitForTimeout(150);
      const currentTop = await page.evaluate((selector) => {
        const node = document.querySelector(selector);
        return node ? Math.round(node.getBoundingClientRect().top) : null;
      }, target);
      if (currentTop !== null && currentTop === previousTop) break;
      previousTop = currentTop ?? Number.NaN;
    }
    const state = await page.evaluate((selector) => {
      const node = document.querySelector(selector);
      return {
        hash: window.location.hash,
        top: node ? Math.round(node.getBoundingClientRect().top) : null,
      };
    }, target);
    audit.step(`nav ${target}`, JSON.stringify(state));
    if (state.hash !== target) {
      audit.low(
        `Nav link ${target} did not update the URL hash`,
        JSON.stringify(state),
      );
    }
    if (state.top === null || state.top < -4 || state.top > 400) {
      audit.medium(
        `Nav link ${target} did not scroll the section into view`,
        `section top=${state.top}px after clicking the link`,
      );
    }
  }

  await context.close();
});

// Screen-reader/DOM structure and colour contrast samples.
test("structure and contrast of the day view", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await enterDayView(page);

  const structure = await page.evaluate(() => {
    const headings = Array.from(document.querySelectorAll("h1,h2,h3,h4,h5,h6")).map(
      (node) => ({
        level: Number(node.tagName.slice(1)),
        text: (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 60),
        name: node.getAttribute("aria-label"),
      }),
    );
    const landmarks = Array.from(
      document.querySelectorAll("main, header, footer, nav, aside, section[aria-label]"),
    ).map((node) => ({
      tag: node.tagName.toLowerCase(),
      label:
        node.getAttribute("aria-label") ||
        node.getAttribute("aria-labelledby") ||
        (node.textContent || "").trim().slice(0, 30),
    }));
    return {
      lang: document.documentElement.lang,
      title: document.title,
      headings,
      landmarks,
      timeElements: document.querySelectorAll("time").length,
      mains: document.querySelectorAll("main").length,
    };
  });
  audit.dump("structure", structure);
  audit.step("structure", structure.title, JSON.stringify(structure));
  if (structure.mains !== 1) {
    audit.low("Day view does not have exactly one main landmark", `found ${structure.mains}`);
  }
  if (!structure.lang) {
    audit.low("html element has no lang attribute", "document.documentElement.lang is empty");
  }
  let previous = 0;
  for (const heading of structure.headings) {
    if (previous && heading.level > previous + 1) {
      audit.nit(
        `Heading level jumps from h${previous} to h${heading.level}`,
        `"${heading.text}" follows a level-${previous} heading`,
      );
    }
    previous = heading.level;
  }

  const contrast = await page.evaluate(() => {
    const parse = (value: string) => {
      const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
      return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
    };
    const luminance = (rgb: number[]) => {
      const [r, g, b] = rgb.map((channel) => {
        const value = channel / 255;
        return value <= 0.03928 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4);
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a: number[], b: number[]) => {
      const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (light + 0.05) / (dark + 0.05);
    };
    const background = (node: HTMLElement) => {
      let current: HTMLElement | null = node;
      while (current) {
        const value = window.getComputedStyle(current).backgroundColor;
        const parsed = parse(value);
        if (parsed && !value.includes("rgba(0, 0, 0, 0")) return parsed;
        current = current.parentElement;
      }
      return [255, 255, 255];
    };
    const samples: {
      selector: string;
      text: string;
      color: string;
      background: string;
      ratio: number;
      size: number;
      weight: string;
    }[] = [];
    const targets: [string, string][] = [
      [".sidebar-tagline", "sidebar tagline"],
      [".small.muted", "muted helper text"],
      [".stat-value", "day stat value"],
      ["#timeline .time", "timeline time"],
      [".eyebrow", "eyebrow label"],
      [".primary", "primary button"],
      ["footer.site-footer span", "footer text"],
    ];
    for (const [selector, label] of targets) {
      const node = document.querySelector<HTMLElement>(selector);
      if (!node) continue;
      const style = window.getComputedStyle(node);
      const foreground = parse(style.color);
      const bg = background(node);
      if (!foreground) continue;
      samples.push({
        selector: `${selector} (${label})`,
        text: (node.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40),
        color: style.color,
        background: `rgb(${bg.join(", ")})`,
        ratio: Math.round(ratio(foreground, bg) * 100) / 100,
        size: Number.parseFloat(style.fontSize),
        weight: style.fontWeight,
      });
    }
    return samples;
  });
  audit.dump("contrast", contrast);
  for (const sample of contrast) {
    const large =
      sample.size >= 24 || (sample.size >= 18.66 && Number(sample.weight) >= 700);
    const threshold = large ? 3 : 4.5;
    if (sample.ratio < threshold) {
      audit.medium(
        `Low contrast: ${sample.selector}`,
        `${sample.ratio}:1 (needs ${threshold}:1 for ${sample.size}px/${sample.weight}) — "${sample.text}"`,
      );
    } else {
      audit.step(`contrast ok ${sample.selector}`, `${sample.ratio}:1`);
    }
  }

  await context.close();
});

test("reduced motion and SPA route fallback", async ({ browser, request }) => {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const sessionId = await createSession();
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key, value),
    [SESSION_KEY, sessionId],
  );
  await page.goto(`${LIVE_BASE}/`, { waitUntil: "domcontentloaded" });
  const motion = await page.evaluate(() => {
    const spinner = document.querySelector<HTMLElement>(".boot-spinner");
    const prefers = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return {
      prefers,
      spinnerAnimation: spinner ? window.getComputedStyle(spinner).animationName : null,
    };
  });
  audit.step("reduced motion", JSON.stringify(motion));
  await expect(page.getByRole("button", { name: /Recheck now/i })).toBeVisible();
  await context.close();

  for (const path of ["/does-not-exist", "/index.html"]) {
    const response = await request.get(`${LIVE_BASE}${path}`);
    const body = await response.text();
    audit.step(
      `route ${path}`,
      String(response.status()),
      squeeze(body).slice(0, 120),
    );
    if (path === "/does-not-exist" && response.status() !== 200) {
      audit.medium(
        `Client-side route ${path} is not served the SPA fallback`,
        `HTTP ${response.status()}; body starts "${squeeze(body).slice(0, 120)}"`,
      );
    }
  }
});
