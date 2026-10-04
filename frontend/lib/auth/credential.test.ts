// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({ cookies: vi.fn() }));
vi.mock("next/navigation", () => ({
  redirect: vi.fn((to: string) => {
    throw new Error(`NEXT_REDIRECT ${to}`);
  }),
}));

import { cookies } from "next/headers";

import { credentialFromCookies, credentialFromRequest, getCredential, loginPath, requireCredential } from "@/lib/auth/credential";

const TOKEN = "S".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";

const DEV = { AUTH_MODE: "dev", APP_ENV: "development" };
const SESSION = { AUTH_MODE: "session", APP_ENV: "development", PUBLIC_ORIGIN: "http://localhost:3000" };
const SESSION_HTTPS = { AUTH_MODE: "session", PUBLIC_ORIGIN: "https://app.example.com" };

function use(values: Record<string, string>) {
  for (const name of ["AUTH_MODE", "APP_ENV", "PUBLIC_ORIGIN"]) vi.stubEnv(name, undefined as unknown as string);
  for (const [name, value] of Object.entries(values)) vi.stubEnv(name, value);
}
const jar = (values: Record<string, string>) => (name: string) => values[name];

afterEach(() => vi.unstubAllEnvs());

describe("credentialFromCookies", () => {
  it("dev mode: the dev-user cookie, normalized; nothing else counts", () => {
    use(DEV);
    expect(credentialFromCookies(jar({ bp_dev_user: " Fredrik@Dev.Test " }))).toEqual({ kind: "dev", email: "fredrik@dev.test" });
    expect(credentialFromCookies(jar({ bp_session: TOKEN }))).toBeNull();
    expect(credentialFromCookies(jar({ bp_dev_user: "not an email" }))).toBeNull();
    expect(credentialFromCookies(jar({}))).toBeNull();
  });

  it("session mode: the session token cookie; the dev cookie has no effect at all", () => {
    use(SESSION);
    expect(credentialFromCookies(jar({ bp_session: TOKEN }))).toEqual({ kind: "session", token: TOKEN });
    expect(credentialFromCookies(jar({ bp_dev_user: "fredrik@dev.test" }))).toBeNull();
    expect(credentialFromCookies(jar({ bp_dev_user: "fredrik@dev.test", bp_session: "short" }))).toBeNull(); // no fallback to the dev cookie
  });

  it.each(["", "short", "S".repeat(42), "S".repeat(44), "S".repeat(42) + "!", "S".repeat(21) + " " + "S".repeat(21)])("session mode ignores the malformed token %j", (value) => {
    use(SESSION);
    expect(credentialFromCookies(jar({ bp_session: value }))).toBeNull();
  });

  it("over https only the __Host- cookie is read", () => {
    use(SESSION_HTTPS);
    expect(credentialFromCookies(jar({ "__Host-bp_session": TOKEN }))).toEqual({ kind: "session", token: TOKEN });
    expect(credentialFromCookies(jar({ bp_session: TOKEN }))).toBeNull();
  });

  it("no mode, no credential: whatever the cookies hold", () => {
    use({});
    expect(credentialFromCookies(jar({ bp_dev_user: "fredrik@dev.test", bp_session: TOKEN }))).toBeNull();
    use({ AUTH_MODE: "dev" }); // APP_ENV missing: production
    expect(credentialFromCookies(jar({ bp_dev_user: "fredrik@dev.test" }))).toBeNull();
  });

  it("is never derived from a request header", () => {
    use(SESSION);
    const request = new NextRequest("http://localhost:3000/api/o/x/customers", {
      headers: { authorization: `Bearer ${TOKEN}`, "x-dev-user-email": "fredrik@dev.test", cookie: "bp_session=" + "x".repeat(10) },
    });
    expect(credentialFromRequest(request)).toBeNull();
    const cookieRequest = new NextRequest("http://localhost:3000/api/o/x/customers", { headers: { cookie: `bp_session=${TOKEN}` } });
    expect(credentialFromRequest(cookieRequest)).toEqual({ kind: "session", token: TOKEN });
  });
});

describe("loginPath", () => {
  it("dev mode always goes to the dev login", () => {
    use(DEV);
    expect(loginPath()).toBe("/dev-login");
    expect(loginPath(`/o/${ORG}`)).toBe("/dev-login");
  });

  it("session mode goes to /login, with a validated relative way back", () => {
    use(SESSION);
    expect(loginPath()).toBe("/login");
    expect(loginPath("/")).toBe("/login");
    expect(loginPath(`/o/${ORG}/customers`)).toBe(`/login?next=${encodeURIComponent(`/o/${ORG}/customers`)}`);
  });

  it("never carries an unsafe way back", () => {
    use(SESSION);
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "/login", "/api/auth/logout"]) expect(loginPath(bad)).toBe("/login");
  });
});

describe("requireCredential and getCredential", () => {
  beforeEach(() => vi.mocked(cookies).mockReset());

  it("returns the credential of the request", async () => {
    use(SESSION);
    vi.mocked(cookies).mockResolvedValue({ get: (name: string) => (name === "bp_session" ? { name, value: TOKEN } : undefined) } as never);
    await expect(requireCredential()).resolves.toEqual({ kind: "session", token: TOKEN });
    await expect(getCredential()).resolves.toEqual({ kind: "session", token: TOKEN });
  });

  it("redirects an unauthenticated visitor to the login of the active mode, with the way back", async () => {
    use(SESSION);
    vi.mocked(cookies).mockResolvedValue({ get: () => undefined } as never);
    await expect(requireCredential(`/o/${ORG}`)).rejects.toThrow(`NEXT_REDIRECT /login?next=${encodeURIComponent(`/o/${ORG}`)}`);
    use(DEV);
    await expect(requireCredential(`/o/${ORG}`)).rejects.toThrow("NEXT_REDIRECT /dev-login");
  });
});
