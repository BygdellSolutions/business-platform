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
