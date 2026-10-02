// @vitest-environment node
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { isSameOrigin } from "@/lib/origin";

function request(headers: Record<string, string>, url = "http://localhost:3100/api/x") {
  return new NextRequest(url, { method: "POST", headers });
}

describe("isSameOrigin", () => {
  it("accepts a request without an Origin header (not a browser cross-site post)", () => {
    expect(isSameOrigin(request({}))).toBe(true);
  });

  it("compares the Origin with the Host the browser used, not with Next's normalized URL host", () => {
    // Next may report localhost while the browser really used 127.0.0.1:
    const req = request({ origin: "http://127.0.0.1:3100", host: "127.0.0.1:3100" }, "http://localhost:3100/api/x");
    expect(isSameOrigin(req)).toBe(true);
  });

  it("falls back to the request URL host when there is no Host header", () => {
    expect(isSameOrigin(request({ origin: "http://localhost:3100" }))).toBe(true);
    expect(isSameOrigin(request({ origin: "http://localhost:4000" }))).toBe(false);
  });

  it.each([
    "http://evil.test",
    "http://127.0.0.1:3100.evil.test",
    "http://evil.test:3100",
    "https://127.0.0.1:3101",
    "null",
    "not a url",
    "",
  ])("rejects the foreign or malformed origin %j", (origin) => {
    expect(isSameOrigin(request({ origin, host: "127.0.0.1:3100" }))).toBe(false);
  });

  it("is not fooled by a Host header on another site's origin", () => {
    expect(isSameOrigin(request({ origin: "http://evil.test", host: "127.0.0.1:3100" }))).toBe(false);
  });
});
