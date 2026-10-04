// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";

import { appEnv, authConfig, authMode, cookiePolicy, devIdentityEnabled, parsePublicOrigin, trustedProxyHops } from "@/lib/auth/config";

afterEach(() => vi.unstubAllEnvs());

function env(values: Record<string, string | undefined>) {
  for (const name of ["AUTH_MODE", "APP_ENV", "PUBLIC_ORIGIN", "DEV_IDENTITY", "TRUSTED_PROXY_HOPS"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(values)) if (value !== undefined) vi.stubEnv(name, value);
}

describe("the authentication mode fails closed", () => {
  it("is none when nothing is configured, and the environment defaults to production", () => {
    env({});
    expect(authConfig()).toMatchObject({ mode: "none", publicOrigin: null });
    expect(authConfig().problem).toMatch(/AUTH_MODE/);
    expect(appEnv()).toBe("production");
  });

  it.each(["", "disabled", "DEV", "Session", "jwt", "dev,session", "true"])("is none for AUTH_MODE=%j", (value) => {
    env({ AUTH_MODE: value, APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" });
    expect(authMode()).toBe("none");
  });

  it("allows the dev identity only with APP_ENV=development", () => {
    env({ AUTH_MODE: "dev", APP_ENV: "development" });
    expect(authMode()).toBe("dev");
    expect(devIdentityEnabled()).toBe(true);
    for (const appEnvValue of [undefined, "", "production", "staging", "Development"]) {
      env({ AUTH_MODE: "dev", APP_ENV: appEnvValue });
      expect(authMode()).toBe("none");
      expect(devIdentityEnabled()).toBe(false);
      expect(authConfig().problem).toMatch(/APP_ENV=development/);
    }
  });

  it("the retired DEV_IDENTITY switch does nothing", () => {
    env({ DEV_IDENTITY: "enabled", APP_ENV: "development" });
    expect(authMode()).toBe("none");
    env({ DEV_IDENTITY: "enabled", AUTH_MODE: "session", APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" });
    expect(authMode()).toBe("session");
    expect(devIdentityEnabled()).toBe(false);
  });

  it("session mode needs a usable PUBLIC_ORIGIN; otherwise there is no identity at all", () => {
    env({ AUTH_MODE: "session", APP_ENV: "development" });
    expect(authMode()).toBe("none");
    expect(authConfig().problem).toMatch(/PUBLIC_ORIGIN/);
    env({ AUTH_MODE: "session", APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" });
    expect(authConfig()).toEqual({ mode: "session", problem: null, publicOrigin: "http://localhost:3000" });
  });

  it("production session mode requires https", () => {
    env({ AUTH_MODE: "session", PUBLIC_ORIGIN: "http://app.example.com" });
    expect(authMode()).toBe("none");
    expect(authConfig().problem).toMatch(/https/);
    env({ AUTH_MODE: "session", PUBLIC_ORIGIN: "https://app.example.com" });
    expect(authConfig()).toMatchObject({ mode: "session", publicOrigin: "https://app.example.com" });
  });
});

describe("parsePublicOrigin", () => {
  it.each([
    ["https://app.example.com", "https://app.example.com"],
    ["https://app.example.com/", "https://app.example.com"],
    [" https://app.example.com:8443 ", "https://app.example.com:8443"],
    ["HTTPS://APP.EXAMPLE.COM", "https://app.example.com"],
  ])("accepts %j as %j", (raw, origin) => {
    expect(parsePublicOrigin(raw, "production")).toEqual({ origin });
  });

  it.each([undefined, "", "   ", "app.example.com", "ftp://app.example.com", "javascript:alert(1)", "https://user:pw@app.example.com", "https://app.example.com/path", "https://app.example.com/?x=1", "https://app.example.com/#x", "not a url"])(
    "rejects %j",
    (raw) => {
      expect(parsePublicOrigin(raw, "development")).toHaveProperty("problem");
    },
  );

  it("allows plain http only in development", () => {
    expect(parsePublicOrigin("http://127.0.0.1:3100", "development")).toEqual({ origin: "http://127.0.0.1:3100" });
    expect(parsePublicOrigin("http://127.0.0.1:3100", "production")).toHaveProperty("problem");
  });
});

describe("the cookie policy follows the canonical origin, not the request", () => {
  it("is __Host- prefixed and Secure over https", () => {
    const policy = cookiePolicy("https://app.example.com");
    expect(policy.secure).toBe(true);
    expect([policy.name("session"), policy.name("csrf"), policy.name("pre")]).toEqual(["__Host-bp_session", "__Host-bp_csrf", "__Host-bp_pre"]);
  });

  it("has the plain names and is not Secure over http (development only)", () => {
    const policy = cookiePolicy("http://localhost:3000");
    expect(policy.secure).toBe(false);
    expect([policy.name("session"), policy.name("csrf"), policy.name("pre")]).toEqual(["bp_session", "bp_csrf", "bp_pre"]);
  });

  it("is derived from the configuration by default", () => {
    env({ AUTH_MODE: "session", PUBLIC_ORIGIN: "https://app.example.com" });
    expect(cookiePolicy().name("session")).toBe("__Host-bp_session");
    env({ AUTH_MODE: "session", APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" });
    expect(cookiePolicy().name("session")).toBe("bp_session");
  });
});

describe("trustedProxyHops", () => {
  it.each([[undefined, 0], ["", 0], ["0", 0], ["1", 1], ["2", 2], [" 3 ", 3], ["-1", 0], ["10", 0], ["x", 0], ["1.5", 0]])("%j gives %i", (raw, hops) => {
    env({ TRUSTED_PROXY_HOPS: raw });
    expect(trustedProxyHops()).toBe(hops);
  });
});
