// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { backendOrigin, backendUrlProblem, bffSecret, bffSecretProblem, isPrivateHost, productionProblems } from "@/lib/runtime-config";

afterEach(() => vi.unstubAllEnvs());

const SECRET = "9f3c1a7e5b2d8046c1e7a95b3d20f648a1c7e903d5b6f2a8";
const GOOD = {
  APP_ENV: "production",
  AUTH_MODE: "session",
  PUBLIC_ORIGIN: "https://app.example.com",
  BACKEND_URL: "http://backend:8000",
  BFF_INTERNAL_SECRET: SECRET,
};

function stub(values: Record<string, string | undefined>) {
  for (const name of ["APP_ENV", "AUTH_MODE", "PUBLIC_ORIGIN", "BACKEND_URL", "BFF_INTERNAL_SECRET", "TRUSTED_PROXY_HOPS"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(values)) if (value !== undefined) vi.stubEnv(name, value);
}

describe("isPrivateHost: where only the private network reaches", () => {
  it.each([
    "backend", "api", "a1b2c3d4-e5f6-7890", "backend.internal", "svc.team.internal", "db.local", "x.lan", "x.home.arpa", "backend.default.svc", "backend.default.svc.cluster.local",
    "10.0.0.5", "10.255.255.255", "172.16.0.1", "172.31.255.254", "192.168.1.20", "fd12:3456:789a::1", "fc00::1", "[fd12::5]", "BACKEND", "backend.",
  ])("accepts %s", (host) => expect(isPrivateHost(host)).toBe(true));

  it.each([
    "", "localhost", "LOCALHOST", "localhost.", "app.localhost", "127.0.0.1", "127.9.9.9", "0.0.0.0", "::1", "[::1]", "::", "169.254.169.254", "fe80::1",
    "8.8.8.8", "203.0.113.5", "172.15.0.1", "172.32.0.1", "11.0.0.1", "192.169.0.1", "2001:db8::1", "api.example.com", "backend.example.test", "backend.internal.example.com", "evil-internal.com", "x.localhost.com",
  ])("refuses %j", (host) => expect(isPrivateHost(host)).toBe(false));
});

describe("backendUrlProblem", () => {
  it.each(["http://backend:8000", "http://backend:8000/", "https://backend.internal", "http://10.1.2.3:8000", "http://[fd00::1]:8000", "  http://backend:8000  "])("accepts %s", (url) => {
    expect(backendUrlProblem(url)).toBeNull();
  });

  it.each([
    [undefined, "required"],
    ["", "required"],
    ["   ", "required"],
    ["not a url", "valid URL"],
    ["ftp://backend:21", "http(s)"],
    ["http://localhost:8000", "private"],
    ["http://127.0.0.1:8000", "private"],
    ["http://[::1]:8000", "private"],
    ["http://0.0.0.0:8000", "private"],
    ["https://api.example.com", "private"],
    ["http://203.0.113.5:8000", "private"],
    ["http://169.254.169.254", "private"],
    ["http://user:pass@backend:8000", "credentials"],
    ["http://backend:8000/api", "origin only"],
    ["http://backend:8000/?x=1", "origin only"],
    ["http://backend:8000/#x", "origin only"],
  ])("refuses %j", (url, why) => {
    expect(backendUrlProblem(url as string | undefined)).toContain(why);
  });
});

describe("backendOrigin: production never falls back to localhost", () => {
  it("development defaults to localhost and strips trailing slashes", () => {
    stub({ APP_ENV: "development" });
    expect(backendOrigin()).toBe("http://localhost:8000");
    stub({ APP_ENV: "development", BACKEND_URL: "http://backend.test:9000///" });
    expect(backendOrigin()).toBe("http://backend.test:9000");
  });

  it.each([undefined, "", "http://localhost:8000", "https://api.example.com"])("production refuses %j", (url) => {
    stub({ ...GOOD, BACKEND_URL: url });
    expect(() => backendOrigin()).toThrow();
  });

  it("unset APP_ENV is production too (nothing falls back)", () => {
    stub({ BACKEND_URL: undefined });
    expect(() => backendOrigin()).toThrow(/BACKEND_URL is required/);
  });

  it("production returns the configured private origin", () => {
    stub(GOOD);
    expect(backendOrigin()).toBe("http://backend:8000");
  });
});

describe("bffSecretProblem", () => {
  it("accepts a long varied secret", () => expect(bffSecretProblem(SECRET)).toBeNull());
  it.each([undefined, ""])("requires one (%j)", (value) => expect(bffSecretProblem(value)).toContain("required"));
  it("refuses a short one", () => expect(bffSecretProblem("a1b2c3")).toContain("at least 32"));
  it.each(["a".repeat(64), "ab".repeat(32), "0123".repeat(16)])("refuses a repetitive one (%#)", (value) => expect(bffSecretProblem(value)).toContain("repetitive"));

  it("bffSecret is the configured value, or null when there is none", () => {
    stub({ BFF_INTERNAL_SECRET: SECRET });
    expect(bffSecret()).toBe(SECRET);
    stub({});
    expect(bffSecret()).toBeNull();
    stub({ BFF_INTERNAL_SECRET: "" });
    expect(bffSecret()).toBeNull();
  });
});

describe("productionProblems: every unsafe production configuration is named", () => {
  it("a complete configuration has none", () => {
    stub(GOOD);
    expect(productionProblems()).toEqual([]);
  });

  it.each([
    ["APP_ENV unset", { APP_ENV: undefined }, "APP_ENV must be set"],
    ["APP_ENV misspelled", { APP_ENV: "prod" }, "APP_ENV must be set"],
    ["AUTH_MODE=dev", { AUTH_MODE: "dev" }, "AUTH_MODE=dev is only allowed"],
    ["AUTH_MODE unset", { AUTH_MODE: undefined }, "AUTH_MODE must be"],
    ["PUBLIC_ORIGIN missing", { PUBLIC_ORIGIN: undefined }, "PUBLIC_ORIGIN is required"],
    ["PUBLIC_ORIGIN http", { PUBLIC_ORIGIN: "http://app.example.com" }, "https"],
    ["BACKEND_URL missing", { BACKEND_URL: undefined }, "BACKEND_URL is required"],
    ["BACKEND_URL localhost", { BACKEND_URL: "http://localhost:8000" }, "BACKEND_URL must be a private"],
    ["BACKEND_URL public", { BACKEND_URL: "https://api.example.com" }, "BACKEND_URL must be a private"],
    ["BFF secret missing", { BFF_INTERNAL_SECRET: undefined }, "BFF_INTERNAL_SECRET is required"],
    ["BFF secret short", { BFF_INTERNAL_SECRET: "short" }, "at least 32"],
    ["TRUSTED_PROXY_HOPS garbage", { TRUSTED_PROXY_HOPS: "two" }, "TRUSTED_PROXY_HOPS"],
  ])("%s", (_name, change, message) => {
    stub({ ...GOOD, ...change });
    expect(productionProblems().join(" | ")).toContain(message);
  });

  it("a valid TRUSTED_PROXY_HOPS is fine, and development reports nothing (its conveniences stay)", () => {
    stub({ ...GOOD, TRUSTED_PROXY_HOPS: "1" });
    expect(productionProblems()).toEqual([]);
    stub({ APP_ENV: "development", AUTH_MODE: "dev" });
    expect(productionProblems()).toEqual([]);
  });
});
