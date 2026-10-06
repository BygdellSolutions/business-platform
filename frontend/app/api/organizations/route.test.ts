// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

/**
 * The BFF door for organization creation (the one route that belongs to no organization). It must build the
 * upstream request from scratch (credential from the protected cookie only), carry no owner, role or
 * organization, apply Origin/CSRF in session mode, forward only a validated retry key, and relay the backend answer.
 * The backend decisions (the flag, the owner, atomicity) are proved by the backend suite and Playwright.
 */

const ORIGIN = "http://127.0.0.1:3100";
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const KEY = "k".repeat(43);
const BODY = JSON.stringify({ name: "Fresh Org", default_currency: "EUR" });

let fetchMock: ReturnType<typeof vi.fn>;
const created = () => Promise.resolve(new Response(JSON.stringify({ id: "x" }), { status: 201, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn().mockImplementation(created);
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function post(options: { cookie?: string | null; headers?: Record<string, string>; body?: string } = {}) {
  const { cookie = "bp_dev_user=fredrik@dev.test", headers = {}, body = BODY } = options;
  const request = new NextRequest(`${ORIGIN}/api/organizations`, {
    method: "POST",
    headers: { origin: ORIGIN, host: "127.0.0.1:3100", "content-type": "application/json", ...(cookie ? { cookie } : {}), ...headers },
    body,
  });
  return POST(request);
}

const upstream = () => fetchMock.mock.calls[0] as [string, { method: string; body: string; headers: Headers }];

describe("development mode", () => {
  beforeEach(() => vi.stubEnv("AUTH_MODE", "dev"));

  it("relays the creation with the dev identity from the cookie and no organization, owner or role", async () => {
    const response = await post({ headers: { "idempotency-key": KEY } });

    expect(response.status).toBe(201);
    const [url, init] = upstream();
    expect(url).toBe("http://backend.test:8000/api/organizations");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(BODY);
    expect(Object.fromEntries(init.headers)).toEqual({ accept: "application/json", "content-type": "application/json", "x-dev-user-email": "fredrik@dev.test", "idempotency-key": KEY, "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("ignores client-supplied identity, organization, role and proxy headers", async () => {
    await post({ headers: { "x-dev-user-email": "maria@dev.test", "x-organization-id": "00000000-0000-4000-8000-0000000000b2", "x-role": "owner", "x-owner-email": "x@y.test", authorization: "Bearer evil", "x-forwarded-for": "6.6.6.6" } });

    expect(Object.fromEntries(upstream()[1].headers)).toEqual({ accept: "application/json", "content-type": "application/json", "x-dev-user-email": "fredrik@dev.test", "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("is a 401 naming the login page without a credential, and never calls the backend", async () => {
    const response = await post({ cookie: null });
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated", login: "/dev-login" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a cross-origin request", async () => {
    expect((await post({ headers: { origin: "https://evil.example" } })).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a malformed retry key and forwards none", async () => {
    for (const bad of ["short", "k".repeat(44), "k".repeat(42) + "!"]) {
      expect((await post({ headers: { "idempotency-key": bad } })).status).toBe(400);
    }
    expect(fetchMock).not.toHaveBeenCalled();
    await post();
    expect(upstream()[1].headers.has("idempotency-key")).toBe(false);
  });

  it("accepts only JSON and a small body", async () => {
    expect((await post({ headers: { "content-type": "text/plain" } })).status).toBe(415);
    expect((await post({ body: "x".repeat(17_000) })).status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("relays the backend refusals as they are (a 403 is not a lost session) and turns a 401 into the fixed answer", async () => {
    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ detail: { code: "organization_creation_not_allowed" } }), { status: 403, headers: { "content-type": "application/json" } })));
    const forbidden = await post();
    expect(forbidden.status).toBe(403);
    expect((await forbidden.json()).detail.code).toBe("organization_creation_not_allowed");

    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response(JSON.stringify({ detail: "Not authenticated", secret: "x" }), { status: 401 })));
    const lost = await post();
    expect(lost.status).toBe(401);
    expect(await lost.json()).toEqual({ detail: "Not authenticated", login: "/dev-login" });
  });

  it("relays no header or cookie from the backend and treats a redirect as an error", async () => {
    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response("{}", { status: 201, headers: { "content-type": "application/json", "set-cookie": "a=b", "x-secret": "1" } })));
    const response = await post();
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-secret")).toBeNull();

    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response(null, { status: 302, headers: { location: "https://evil.example" } })));
    expect((await post()).status).toBe(502);
  });

  it("is a 502 when the backend cannot be reached", async () => {
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error("down")));
    expect((await post()).status).toBe(502);
  });
});

describe("session mode", () => {
  beforeEach(() => {
    vi.stubEnv("AUTH_MODE", "session");
    vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  });
  const signedIn = `bp_session=${SESSION}; bp_csrf=${CSRF}`;
  const csrf = { "x-csrf-token": CSRF };

  it("sends Authorization: Bearer from the session cookie and the validated CSRF token, and nothing that names a user", async () => {
    const response = await post({ cookie: signedIn, headers: { ...csrf, "idempotency-key": KEY, "x-dev-user-email": "maria@dev.test", "x-organization-id": "00000000-0000-4000-8000-0000000000b2" } });

    expect(response.status).toBe(201);
    expect(Object.fromEntries(upstream()[1].headers)).toEqual({ accept: "application/json", "content-type": "application/json", authorization: `Bearer ${SESSION}`, "x-csrf-token": CSRF, "idempotency-key": KEY, "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("refuses a missing, mismatched or absent CSRF token before contacting the backend", async () => {
    expect((await post({ cookie: signedIn })).status).toBe(403);
    expect((await post({ cookie: signedIn, headers: { "x-csrf-token": "E".repeat(43) } })).status).toBe(403);
    expect((await post({ cookie: `bp_session=${SESSION}`, headers: csrf })).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("checks Origin against PUBLIC_ORIGIN, not Host", async () => {
    expect((await post({ cookie: signedIn, headers: { ...csrf, origin: "https://evil.example", host: "evil.example" } })).status).toBe(403);
    expect((await post({ cookie: signedIn, headers: { ...csrf, origin: "" } })).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is a 401 naming the login page without a session", async () => {
    const response = await post({ cookie: null, headers: csrf });
    expect(response.status).toBe(401);
    expect((await response.json()).login).toBe("/login");
  });
});

describe("no authentication configured", () => {
  it("answers 503 and does nothing", async () => {
    vi.stubEnv("AUTH_MODE", "");
    expect((await post()).status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
