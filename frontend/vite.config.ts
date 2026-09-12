import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(frontendDir, "..");

/**
 * The build the bundle is running and, for a deployed build, what the page
 * compares itself against. Kept in sync with the API by scripts/version.py:
 * VERSION is the number every declaration has to match.
 */
interface BuildInfo {
  version: string;
  commit: string;
  dirty: boolean;
  builtAt: string;
  mode: "production" | "development";
}

function readVersion(): string {
  const fallback = JSON.parse(
    readFileSync(path.join(frontendDir, "package.json"), "utf8"),
  ).version as string;
  try {
    const value = readFileSync(path.join(repoRoot, "VERSION"), "utf8").trim();
    return /^\d+\.\d+\.\d+$/.test(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function readCommit(): { commit: string; dirty: boolean } {
  const git = (args: string[]): string =>
    execFileSync("git", args, {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  try {
    return {
      commit: git(["rev-parse", "--short", "HEAD"]),
      dirty: git(["status", "--porcelain"]).length > 0,
    };
  } catch {
    // A source archive without .git still has to build, so fall back to the
    // commit CI checked out. The badge says "unknown" rather than inventing one.
    return { commit: process.env.GITHUB_SHA?.slice(0, 7) ?? "unknown", dirty: false };
  }
}

function buildInfo(mode: BuildInfo["mode"]): BuildInfo {
  return {
    version: readVersion(),
    ...readCommit(),
    builtAt: new Date().toISOString(),
    mode,
  };
}

/**
 * Publish the build's own identity as /version.json.
 *
 * CloudFront revalidates the SPA shell on every request and the version
 * manifest is served through the same no-cache behavior, so an open tab can
 * poll this file, notice a deploy it did not load, and offer a reload. The
 * dev server answers with the same manifest so local work never reports a
 * phantom update.
 */
function versionManifest(info: BuildInfo): Plugin {
  const manifest = `${JSON.stringify(info, null, 2)}\n`;
  return {
    name: "glide-version-manifest",
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if ((request.url ?? "").split("?")[0] !== "/version.json") {
          next();
          return;
        }
        response.setHeader("Content-Type", "application/json");
        response.setHeader("Cache-Control", "no-store");
        response.end(manifest);
      });
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "version.json", source: manifest });
    },
  };
}

export default defineConfig(({ command }) => {
  const info = buildInfo(command === "build" ? "production" : "development");
  return {
    plugins: [react(), versionManifest(info)],
    // The interface reads its own build identity through this constant; the
    // fallback in src/version.ts keeps tools that import the module (the
    // verify script, tests) working outside a Vite build.
    define: { __GLIDE_BUILD__: JSON.stringify(info) },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:8000",
          changeOrigin: true,
        },
      },
    },
  };
});
