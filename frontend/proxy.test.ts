// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { config, proxy } from "./proxy";

afterEach(() => vi.unstubAllEnvs());

function hsts(env: Record<string, string | undefined>): string | null {
  for (const name of ["APP_ENV", "AUTH_MODE", "PUBLIC_ORIGIN"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(env)) if (value !== undefined) vi.stubEnv(name, value);
  return proxy().headers.get("strict-transport-security");
}

describe("HSTS: production over https only", () => {
  it("is emitted in production with an https PUBLIC_ORIGIN: one year, no includeSubDomains, no preload", () => {
    const value = hsts({ APP_ENV: "production", AUTH_MODE: "session", PUBLIC_ORIGIN: "https://app.example.com" });
    expect(value).toBe("max-age=31536000");
    expect(value).not.toMatch(/includeSubDomains|preload/i);
  });

  it.each([
    ["development over plain http", { APP_ENV: "development", AUTH_MODE: "session", PUBLIC_ORIGIN: "http://127.0.0.1:3100" }],
    ["development even with an https origin", { APP_ENV: "development", AUTH_MODE: "session", PUBLIC_ORIGIN: "https://dev.example.com" }],
    ["the dev identity", { APP_ENV: "development", AUTH_MODE: "dev" }],
    ["production with an http origin (refused at startup, never HSTS)", { APP_ENV: "production", AUTH_MODE: "session", PUBLIC_ORIGIN: "http://app.example.com" }],
    ["production without an origin", { APP_ENV: "production", AUTH_MODE: "session" }],
    ["nothing configured", {}],
  ])("is NOT emitted for %s", (_name, env) => {
    expect(hsts(env)).toBeNull();
  });

  it("decides per request from the runtime environment (the image is built once)", () => {
    expect(hsts({ APP_ENV: "production", AUTH_MODE: "session", PUBLIC_ORIGIN: "https://a.example.com" })).not.toBeNull();
    expect(hsts({ APP_ENV: "development", AUTH_MODE: "session", PUBLIC_ORIGIN: "http://localhost:3000" })).toBeNull();
  });
});

describe("the proxy matcher", () => {
  it("covers pages and API routes but not the build's static files", () => {
    const pattern = new RegExp(`^${config.matcher[0].replace(/\(\?!/, "(?!")}$`);
    for (const path of ["/", "/login", "/api/health", "/o/abc/customers"]) expect(pattern.test(path), path).toBe(true);
    for (const path of ["/_next/static/chunks/a.js", "/_next/image", "/favicon.ico"]) expect(pattern.test(path), path).toBe(false);
  });
});
