// The build this tab is running, and the comparison the version monitor makes
// against whatever is deployed right now.
//
// Deliberately framework-free and import-free: vite.config.ts injects
// __GLIDE_BUILD__ and publishes the same object as /version.json, and
// scripts/verify-version.mjs imports this module directly in Node.

export interface BuildInfo {
  version: string;
  commit: string;
  dirty: boolean;
  builtAt: string;
  mode: "production" | "development";
}

/** What a deployed build says about itself. */
export interface VersionReport {
  version: string;
  commit: string;
  builtAt: string;
}

export type MonitorStatus = "checking" | "current" | "outdated" | "unavailable";

declare const __GLIDE_BUILD__: BuildInfo;

const FALLBACK: BuildInfo = {
  version: "0.0.0-dev",
  commit: "unknown",
  dirty: true,
  builtAt: "",
  mode: "development",
};

export const buildInfo: BuildInfo =
  typeof __GLIDE_BUILD__ === "undefined" ? FALLBACK : __GLIDE_BUILD__;

export const CHANGELOG_URL =
  "https://github.com/harveybellini/glide/blob/main/CHANGELOG.md";

const VERSION_URL = "/version.json";
const HEALTH_URL = "/api/health";

/**
 * Read the deployed build's identity, or null when the host has no manifest
 * yet (a deploy that predates this feature) or the tab is offline.
 */
export async function fetchDeployedVersion(): Promise<VersionReport | null> {
  try {
    const response = await fetch(VERSION_URL, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as Partial<VersionReport>;
    if (typeof payload.version !== "string" || typeof payload.commit !== "string") {
      return null;
    }
    return {
      version: payload.version,
      commit: payload.commit,
      builtAt: typeof payload.builtAt === "string" ? payload.builtAt : "",
    };
  } catch {
    return null;
  }
}

/** The API's own version, so a bundle/API drift is visible on the page too. */
export async function fetchApiVersion(): Promise<string | null> {
  try {
    const response = await fetch(HEALTH_URL, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return null;
    }
    const payload = (await response.json()) as { version?: unknown };
    return typeof payload.version === "string" ? payload.version : null;
  } catch {
    return null;
  }
}

/** True when `deployed` is a different build from the one already running. */
export function isDifferentBuild(
  deployed: VersionReport | null,
  current: BuildInfo | VersionReport,
): boolean {
  if (!deployed) {
    return false;
  }
  if (deployed.version !== current.version) {
    return true;
  }
  if (!isKnownCommit(deployed.commit) || !isKnownCommit(current.commit)) {
    return false;
  }
  return deployed.commit !== current.commit;
}

/** True when the deployed build is a different build from this tab's. */
export function differsFromBuild(deployed: VersionReport | null): boolean {
  return isDifferentBuild(deployed, buildInfo);
}

/**
 * "unknown"/"dev" are honest gaps, not evidence of a new build, so a host that
 * cannot report a commit never nags the visitor.
 */
export function isKnownCommit(value: string): boolean {
  return Boolean(value) && value !== "unknown" && value !== "dev";
}

export function describeStatus(
  status: MonitorStatus,
  deployed: VersionReport | null,
): string {
  switch (status) {
    case "checking":
      return "Checking the deployed build…";
    case "current":
      return "Up to date";
    case "unavailable":
      return "Deployed build not reporting";
    default:
      return deployed
        ? `v${deployed.version} (${deployed.commit}) is live`
        : "A newer build is live";
  }
}

export function formatBuildTime(value: string): string {
  if (!value) {
    return "unknown";
  }
  const when = new Date(value);
  if (Number.isNaN(when.getTime())) {
    return "unknown";
  }
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(when);
}

export function formatClock(value: Date): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(value);
}
