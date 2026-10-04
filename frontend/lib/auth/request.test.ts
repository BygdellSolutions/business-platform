// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clientAddress, originProblem, safeEqual, validCsrf, validPreAuth } from "@/lib/auth/request";

const ORIGIN = "http://127.0.0.1:3100";
const TOKEN_A = "A".repeat(43);
const TOKEN_B = "B".repeat(43);

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
});
afterEach(() => vi.unstubAllEnvs());

function request(headers: Record<string, string> = {}, cookies: Record<string, string> = {}) {
  const cookie = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
  return new NextRequest(`${ORIGIN}/api/o/x/customers`, { method: "POST", headers: { ...(cookie ? { cookie } : {}), ...headers } });
}

describe("originProblem: the canonical origin decides, not the Host header", () => {
  it("accepts exactly PUBLIC_ORIGIN", () => {
    expect(originProblem(request({ origin: ORIGIN }))).toBeNull();
  });

  it.each([
    ["http://evil.example", "mismatch"],
    ["http://127.0.0.1:3101", "mismatch"],
    ["https://127.0.0.1:3100", "mismatch"],
    ["http://127.0.0.1", "mismatch"],
    [ORIGIN + "/", "mismatch"],
    ["null", "mismatch"],
    ["", "missing"],
  ])("refuses the origin %j (%s)", (origin, kind) => {
    expect(originProblem(request({ origin }))).toBe(kind);
  });

  it("refuses a request with no Origin at all", () => {
    expect(originProblem(request())).toBe("missing");
  });

  it("does not trust Host or forwarded hosts: a matching Host with a foreign Origin is refused, a foreign Host with the right Origin is fine", () => {
    expect(originProblem(request({ origin: "http://evil.example", host: "evil.example" }))).toBe("mismatch");
    expect(originProblem(request({ origin: "http://evil.example", host: "127.0.0.1:3100", "x-forwarded-host": "127.0.0.1:3100" }))).toBe("mismatch");
    expect(originProblem(request({ origin: ORIGIN, host: "evil.example", "x-forwarded-host": "evil.example" }))).toBeNull();
  });

  it("is unconfigured, and therefore refuses, when PUBLIC_ORIGIN is unusable", () => {
    vi.stubEnv("PUBLIC_ORIGIN", "");
    expect(originProblem(request({ origin: ORIGIN }))).toBe("unconfigured");
  });

  it("in production only the https canonical origin is accepted", () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    expect(originProblem(request({ origin: "https://app.example.com" }))).toBeNull();
    expect(originProblem(request({ origin: "http://app.example.com" }))).toBe("mismatch");
  });
});

describe("validCsrf: the double-submit pair", () => {
  it("accepts a header equal to the CSRF cookie and returns the token", () => {
    expect(validCsrf(request({ "x-csrf-token": TOKEN_A }, { bp_csrf: TOKEN_A }))).toBe(TOKEN_A);
  });

  it.each([
    ["no header", {}, { bp_csrf: TOKEN_A }],
    ["no cookie", { "x-csrf-token": TOKEN_A }, {}],
    ["different values", { "x-csrf-token": TOKEN_A }, { bp_csrf: TOKEN_B }],
    ["a malformed header", { "x-csrf-token": "short" }, { bp_csrf: "short" }],
    ["a malformed cookie", { "x-csrf-token": TOKEN_A }, { bp_csrf: TOKEN_A + "x" }],
    ["the session cookie in place of the CSRF cookie", { "x-csrf-token": TOKEN_A }, { bp_session: TOKEN_A }],
    ["the pre-auth cookie in place of the CSRF cookie", { "x-csrf-token": TOKEN_A }, { bp_pre: TOKEN_A }],
  ])("refuses %s", (_name, headers, cookies) => {
    expect(validCsrf(request(headers, cookies))).toBeNull();
  });

  it("uses the __Host- cookie name over https", () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    expect(validCsrf(request({ "x-csrf-token": TOKEN_A }, { "__Host-bp_csrf": TOKEN_A }))).toBe(TOKEN_A);
    expect(validCsrf(request({ "x-csrf-token": TOKEN_A }, { bp_csrf: TOKEN_A }))).toBeNull(); // the unprefixed name is not accepted
  });
});

describe("validPreAuth", () => {
  it("needs the header to equal the pre-auth cookie", () => {
    expect(validPreAuth(request({ "x-pre-auth": TOKEN_A }, { bp_pre: TOKEN_A }))).toBe(true);
    expect(validPreAuth(request({ "x-pre-auth": TOKEN_A }, { bp_pre: TOKEN_B }))).toBe(false);
    expect(validPreAuth(request({}, { bp_pre: TOKEN_A }))).toBe(false);
    expect(validPreAuth(request({ "x-pre-auth": TOKEN_A }, {}))).toBe(false);
    expect(validPreAuth(request({ "x-pre-auth": TOKEN_A }, { bp_csrf: TOKEN_A }))).toBe(false); // a session's CSRF cookie is not a pre-auth secret
  });
});

describe("safeEqual", () => {
  it("is exact and length-safe", () => {
    expect(safeEqual("abc", "abc")).toBe(true);
    expect(safeEqual("abc", "abd")).toBe(false);
    expect(safeEqual("abc", "abcd")).toBe(false);
    expect(safeEqual("", "")).toBe(true);
  });
});

describe("clientAddress: the browser cannot choose the value", () => {
  const headers = (xff: string) => new Headers({ "x-forwarded-for": xff });

  it("forwards nothing with no trusted proxy (the default)", () => {
    expect(clientAddress(headers("198.51.100.7"), 0)).toBeNull();
  });

  it("takes the entry the outermost trusted proxy appended, never what the client wrote", () => {
    expect(clientAddress(headers("198.51.100.7"), 1)).toBe("198.51.100.7");
    expect(clientAddress(headers("6.6.6.6, 198.51.100.7"), 1)).toBe("198.51.100.7"); // 6.6.6.6 was written by the client
    expect(clientAddress(headers("6.6.6.6, 198.51.100.7, 10.0.0.1"), 2)).toBe("198.51.100.7");
    expect(clientAddress(headers("2001:db8::1"), 1)).toBe("2001:db8::1");
  });

  it("gives nothing when there are fewer entries than trusted hops, or the entry is not an address", () => {
    expect(clientAddress(headers("198.51.100.7"), 2)).toBeNull();
    expect(clientAddress(new Headers(), 1)).toBeNull();
    expect(clientAddress(headers("not-an-ip"), 1)).toBeNull();
    expect(clientAddress(headers("198.51.100.7, <script>"), 1)).toBeNull();
  });

  it("reads TRUSTED_PROXY_HOPS by default", () => {
    vi.stubEnv("TRUSTED_PROXY_HOPS", "1");
    expect(clientAddress(headers("6.6.6.6, 198.51.100.7"))).toBe("198.51.100.7");
    vi.stubEnv("TRUSTED_PROXY_HOPS", "0");
    expect(clientAddress(headers("198.51.100.7"))).toBeNull();
  });
});
