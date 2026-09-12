// Verifies the version monitor's decision logic without a browser.
//
// The badge must offer a reload when a deploy moves on, and must stay quiet
// when it cannot tell (an older host with no version.json, an unknown commit).
// Node strips the TypeScript types, so this exercises the module the UI ships.
import assert from "node:assert/strict";

const version = await import("../src/version.ts");

const {
  buildInfo,
  differsFromBuild,
  isDifferentBuild,
  describeStatus,
  formatBuildTime,
  isKnownCommit,
} = version;

assert.match(buildInfo.version, /^\d+\.\d+\.\d+/, "the build reports a version");
assert.equal(
  differsFromBuild({ ...buildInfo }),
  false,
  "the build compares equal to itself",
);
// Synthetic builds cover the comparison exhaustively; the running build may
// legitimately report commit "unknown" outside a Vite build.
const running = { version: "0.2.0", commit: "aaa1111", builtAt: "" };
assert.equal(isDifferentBuild({ ...running }, running), false);
assert.equal(
  isDifferentBuild({ ...running, commit: "bbb2222" }, running),
  true,
  "a different commit under the same version is a new build",
);
assert.equal(
  isDifferentBuild({ ...running, version: "0.3.0" }, running),
  true,
  "a different version is a new build",
);
assert.equal(
  isDifferentBuild({ ...running, commit: "unknown" }, running),
  false,
  "an unknown commit never fakes an update",
);
assert.equal(isDifferentBuild(null, running), false, "a host with no manifest stays quiet");
assert.equal(isKnownCommit(""), false);
assert.equal(isKnownCommit("dev"), false);
assert.equal(isKnownCommit("abc1234"), true);
assert.match(describeStatus("current", null), /Up to date/);
assert.match(describeStatus("outdated", { version: "9.9.9", commit: "abc1234", builtAt: "" }), /9\.9\.9/);
assert.equal(formatBuildTime("not-a-date"), "unknown");
assert.notEqual(formatBuildTime("2026-09-12T10:30:00.000Z"), "unknown");

console.log("version monitor: comparison and status copy behave");
