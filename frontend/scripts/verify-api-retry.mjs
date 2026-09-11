// Verifies the transport-retry policy in src/api.ts without a browser.
//
// A lost response must never duplicate a run, so only idempotent verbs may be
// retried. Node >= 22.6 strips TypeScript types (run with
// --experimental-strip-types on Node 22; Node >= 23 does it by default), which
// lets this script exercise the real module the UI ships.
import assert from "node:assert/strict";

const stored = new Map([["glide-sample-session", "session-1"]]);
globalThis.localStorage = {
  getItem: (key) => (stored.has(key) ? stored.get(key) : null),
  setItem: (key, value) => stored.set(key, String(value)),
  removeItem: (key) => stored.delete(key),
};

const calls = [];
globalThis.fetch = async (path, init = {}) => {
  const method = (init.method ?? "GET").toUpperCase();
  calls.push(`${method} ${path}`);
  const attempts = calls.filter((call) => call === `${method} ${path}`).length;
  if (method !== "GET") {
    throw new TypeError("simulated transport failure");
  }
  if (attempts === 1) {
    throw new TypeError("simulated transient failure");
  }
  return {
    ok: true,
    status: 200,
    json: async () => ({ date: "2026-09-09", label: "Sample calendar" }),
  };
};

const api = await import("../src/api.ts");

let postError = null;
try {
  await api.runCheck();
} catch (error) {
  postError = error;
}

const postAttempts = calls.filter((call) => call === "POST /api/runs").length;
assert.ok(postError instanceof TypeError, "a failed POST must be surfaced");
assert.equal(postAttempts, 1, "POST /api/runs must be attempted exactly once");

const day = await api.fetchDay();

const getAttempts = calls.filter((call) => call === "GET /api/day").length;
assert.equal(getAttempts, 2, "an idempotent GET must be retried once");
assert.equal(day.date, "2026-09-09");

console.log("api retry policy: POST attempted once, GET retried once");
