import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";

test("deployed API enforces settings bounds", async ({ request }) => {
  const created = await request.post("/api/demo/session", { data: {} });
  expect(created.status()).toBe(201);
  const sessionId = (await created.json()).session.session_id as string;
  const headers = { "X-Glide-Session": sessionId };

  const day = await request.get("/api/day", { headers });
  expect(day.status()).toBe(200);
  expect((await day.json()).source_events).toHaveLength(3);

  const me = await request.get("/api/me", { headers });
  expect(me.status()).toBe(200);
  expect((await me.json()).padding_minutes).toBe(10);

  for (const value of [61, 99, -1]) {
    const response = await request.patch("/api/settings", {
      headers,
      data: { padding_minutes: value },
    });
    console.log(
      `PATCH /api/settings padding_minutes=${value} ->`,
      response.status(),
      (await response.text()).slice(0, 300),
    );
    expect(response.status()).toBe(422);
  }

  const valid = await request.patch("/api/settings", {
    headers,
    data: { padding_minutes: 60 },
  });
  expect(valid.status()).toBe(200);
  expect((await valid.json()).padding_minutes).toBe(60);
});

test("undeployed API paths fail as API errors, not the SPA shell", async ({
  request,
}) => {
  const response = await request.get("/api/does-not-exist");
  const body = await response.text();
  console.log(
    "GET /api/does-not-exist ->",
    response.status(),
    response.headers()["content-type"],
    body.slice(0, 120).replace(/\s+/g, " "),
  );
  expect(response.status()).toBe(404);
});

test("deployed shell exposes cache, security, and favicon metadata", async ({
  request,
}) => {
  const response = await request.get("/");
  expect(response.status()).toBe(200);
  const headers = response.headers();
  const html = await response.text();
  const scripts = [...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map(
    (match) => match[1],
  );
  const icon = html.match(/<link[^>]+rel="icon"[^>]*>/i)?.[0] ?? null;

  console.log(
    "live shell:",
    JSON.stringify(
      {
        scripts,
        icon,
        cacheControl: headers["cache-control"] ?? null,
        contentSecurityPolicy: headers["content-security-policy"] ?? null,
        strictTransportSecurity: headers["strict-transport-security"] ?? null,
        xContentTypeOptions: headers["x-content-type-options"] ?? null,
        referrerPolicy: headers["referrer-policy"] ?? null,
      },
      null,
      2,
    ),
  );

  expect(scripts.length).toBeGreaterThan(0);
  expect(icon).not.toBeNull();
  expect(icon).toContain("image/svg+xml");
});

test("deployed assets match the checked-in production build", async ({
  request,
}) => {
  const response = await request.get("/");
  const html = await response.text();
  const localHtml = readFileSync(
    path.resolve(process.cwd(), "dist", "index.html"),
    "utf8",
  );
  const liveAssets = [...html.matchAll(/\/assets\/([^"]+\.(?:js|css))/g)].map(
    (match) => match[1],
  );
  const localAssets = [
    ...localHtml.matchAll(/\/assets\/([^"]+\.(?:js|css))/g),
  ].map((match) => match[1]);

  expect(liveAssets.length).toBeGreaterThan(0);
  expect(localAssets.length).toBeGreaterThan(0);
  console.log("live assets:", liveAssets.join(", "));
  console.log("local assets:", localAssets.join(", "));
  expect(liveAssets.sort()).toEqual(localAssets.sort());

  for (const asset of liveAssets) {
    const assetResponse = await request.get(`/assets/${asset}`);
    expect(assetResponse.status()).toBe(200);
    const liveHash = createHash("sha256")
      .update(await assetResponse.body())
      .digest("hex");
    const localHash = createHash("sha256")
      .update(readFileSync(path.resolve(process.cwd(), "dist", "assets", asset)))
      .digest("hex");
    console.log(asset, "sha256", liveHash, "local", localHash);
    expect(liveHash).toBe(localHash);
  }
});

test("deployed bundle contains the idempotent-retry guard", async ({
  request,
}) => {
  const html = await (await request.get("/")).text();
  const script = html.match(/<script[^>]+src="(\/assets\/[^"]+\.js)"/)?.[1];
  expect(script).toBeTruthy();
  const bundle = await (await request.get(script!)).text();
  const index = bundle.indexOf("OPTIONS");
  console.log(
    "safe retry marker:",
    index,
    index >= 0 ? bundle.slice(Math.max(0, index - 140), index + 140) : "(absent)",
  );
  expect(bundle).toContain('"GET","HEAD","OPTIONS"');
});
