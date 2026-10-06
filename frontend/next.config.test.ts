// @vitest-environment node
import { describe, expect, it } from "vitest";

import nextConfig from "./next.config";

describe("the secret-bearing pages and the authentication API are never cached and send no referrer", () => {
  it("covers /login, /setup and /api/auth/*", async () => {
    const rules = await nextConfig.headers!();
    const bySource = Object.fromEntries(rules.map((rule) => [rule.source, Object.fromEntries(rule.headers.map((h) => [h.key, h.value]))]));
    for (const source of ["/login", "/setup", "/api/auth/:path*"]) {
      expect(bySource[source], source).toEqual({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" });
    }
  });
});

describe("the baseline security headers", () => {
  const rulesFor = async () => (await nextConfig.headers!()).map((rule) => ({ source: rule.source, headers: Object.fromEntries(rule.headers.map((h) => [h.key, h.value])) }));

  it("apply to every path first, so later rules can tighten them (the last value for a header wins)", async () => {
    const rules = await rulesFor();
    expect(rules[0].source).toBe("/:path*");
    expect(rules[0].headers).toMatchObject({
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "strict-origin-when-cross-origin",
      "X-Frame-Options": "DENY",
    });
    expect(rules[0].headers["Permissions-Policy"]).toMatch(/camera=\(\)/);
    expect(rules[0].headers["Permissions-Policy"]).toMatch(/microphone=\(\)/);
    expect(rules[0].headers["Permissions-Policy"]).toMatch(/geolocation=\(\)/);
  });

  it("contain neither HSTS (a runtime decision, see proxy.ts) nor a Content-Security-Policy (tracked separately)", async () => {
    for (const rule of await rulesFor()) {
      expect(Object.keys(rule.headers).map((k) => k.toLowerCase())).not.toContain("strict-transport-security");
      expect(Object.keys(rule.headers).map((k) => k.toLowerCase())).not.toContain("content-security-policy");
    }
  });

  it("the secret-bearing pages and the invite API keep the STRONGER no-referrer and no-store, after the baseline", async () => {
    const rules = await rulesFor();
    const baseline = rules.findIndex((rule) => rule.source === "/:path*");
    for (const source of ["/login", "/setup", "/invite", "/api/invite/:path*", "/api/auth/:path*"]) {
      const index = rules.findIndex((rule) => rule.source === source);
      expect(index, source).toBeGreaterThan(baseline); // later rules override earlier ones
      expect(rules[index].headers, source).toEqual({ "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" });
    }
  });
});
