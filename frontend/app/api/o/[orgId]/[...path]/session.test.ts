// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as route from "./route";

/**
 * The BFF in SESSION mode: upstream authentication is built from the protected cookie only, the browser half of
 * CSRF (Origin against PUBLIC_ORIGIN, the double-submit pair) is enforced here, and nothing the client says
 * about identity, tenant or trust reaches FastAPI. FastAPI's own checks are proved in the backend suite.
 */

const ORG = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ORG = "00000000-0000-4000-8000-0000000000b2";
const ORIGIN = "http://127.0.0.1:3100";
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const EVIL = "E".repeat(43);

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } })));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

interface Options {
  path?: string[];
  headers?: Record<string, string>;
  cookies?: Record<string, string> | null; // null: none at all
  body?: string;
  org?: string;
}

const SIGNED_IN = { bp_session: SESSION, bp_csrf: CSRF };

async function call(method: "GET" | "POST" | "PATCH" | "DELETE", options: Options = {}) {
  const { path = ["customers"], headers = {}, cookies = SIGNED_IN, body, org = ORG } = options;
  const cookie = cookies === null ? "" : Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
  const request = new NextRequest(`${ORIGIN}/api/o/${org}/${path.join("/")}`, { method, headers: { ...(cookie ? { cookie } : {}), ...headers }, body });
  return route[method](request, { params: Promise.resolve({ orgId: org, path }) });
}

const MUTATION = { headers: { origin: ORIGIN, "x-csrf-token": CSRF, "content-type": "application/json" }, body: "{}" };

function sentHeaders(index = 0): Headers {
  return (fetchMock.mock.calls[index][1] as { headers: Headers }).headers;
}

describe("upstream authentication is built from the protected cookie", () => {
  it("sends Authorization: Bearer <session cookie> and the organization from the URL, and nothing that names a user", async () => {
    await call("GET");

    const headers = Object.fromEntries(sentHeaders());
    expect(headers).toEqual({ accept: "application/json", authorization: `Bearer ${SESSION}`, "x-organization-id": ORG, "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("ignores a client-supplied Authorization header", async () => {
    await call("GET", { headers: { authorization: `Bearer ${EVIL}` } });
    expect(sentHeaders().get("authorization")).toBe(`Bearer ${SESSION}`);
  });

  it("does not proxy the client's Cookie header or any identity, role, tenant or proxy header", async () => {
    await call("GET", {
      headers: {
        "x-dev-user-email": "maria@dev.test",
        "x-organization-id": OTHER_ORG,
        "x-user-id": "1",
        "x-user-role": "owner",
        "x-role": "owner",
        "x-forwarded-for": "6.6.6.6",
        "x-forwarded-host": "evil.example",
        "x-forwarded-proto": "https",
        "x-real-ip": "6.6.6.6",
        forwarded: "for=6.6.6.6",
        "x-client-ip": "6.6.6.6",
        "x-csrf-token": EVIL,
        "x-pre-auth": EVIL,
      },
    });

    const names = [...sentHeaders().keys()].sort();
    expect(names).toEqual(["accept", "authorization", "x-organization-id", "x-request-id"]);
    expect(sentHeaders().get("x-organization-id")).toBe(ORG);
    expect(sentHeaders().has("cookie")).toBe(false);
  });

  it("takes the organization only from the URL, whatever header the client sends", async () => {
    await call("GET", { org: OTHER_ORG, headers: { "x-organization-id": ORG } });
    expect(sentHeaders().get("x-organization-id")).toBe(OTHER_ORG);
  });

  it("the dev cookie is not an identity in session mode, and neither is a dev header: no backend call", async () => {
    const response = await call("GET", { cookies: { bp_dev_user: "fredrik@dev.test" }, headers: { "x-dev-user-email": "fredrik@dev.test" } });
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated", login: "/login" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each<Record<string, string> | null>([null, {}, { bp_session: "short" }, { bp_session: "S".repeat(44) }, { bp_csrf: CSRF }])("a missing or malformed session cookie (%j) is 401 without calling the backend", async (cookies) => {
    expect((await call("GET", { cookies })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("never sends a token to the browser: no Authorization, cookie or set-cookie in the response", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 200, headers: { "content-type": "application/json", "set-cookie": "session=abc", authorization: "Bearer x" } }));
    const response = await call("GET");
    for (const name of ["set-cookie", "authorization", "cookie", "www-authenticate"]) expect(response.headers.get(name)).toBeNull();
    expect(JSON.stringify([...response.headers])).not.toContain(SESSION);
  });
});

describe("state-changing requests: the browser half of CSRF", () => {
  it("forwards the validated CSRF token, with the session, when Origin and the double-submit pair are right", async () => {
    const response = await call("POST", MUTATION);

    expect(response.status).toBe(200);
    expect(sentHeaders().get("x-csrf-token")).toBe(CSRF);
    expect(sentHeaders().get("authorization")).toBe(`Bearer ${SESSION}`);
  });

  it.each(["PATCH", "DELETE"] as const)("%s needs the same", async (method) => {
    expect((await call(method, { ...MUTATION, headers: { origin: ORIGIN, "content-type": "application/json" } })).status).toBe(403);
    expect((await call(method, MUTATION)).status).toBe(200);
  });

  it("refuses a missing Origin (every browser mutation carries one)", async () => {
    const response = await call("POST", { ...MUTATION, headers: { "x-csrf-token": CSRF, "content-type": "application/json" } });
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["http://evil.example", "http://127.0.0.1:3101", "https://127.0.0.1:3100", "null", ORIGIN + "/"])("refuses the Origin %j", async (origin) => {
    const response = await call("POST", { ...MUTATION, headers: { ...MUTATION.headers, origin } });
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("decides by PUBLIC_ORIGIN, not by Host: a foreign Origin that matches Host is refused, the right Origin with a foreign Host is accepted", async () => {
    expect((await call("POST", { ...MUTATION, headers: { ...MUTATION.headers, origin: "http://evil.example", host: "evil.example" } })).status).toBe(403);
    expect((await call("POST", { ...MUTATION, headers: { ...MUTATION.headers, host: "evil.example" } })).status).toBe(200);
  });

  it.each([
    ["no CSRF header", { origin: ORIGIN, "content-type": "application/json" }, SIGNED_IN],
    ["a CSRF header that differs from the cookie", { ...MUTATION.headers, "x-csrf-token": EVIL }, SIGNED_IN],
    ["no CSRF cookie", MUTATION.headers, { bp_session: SESSION }],
    ["a malformed CSRF pair", { ...MUTATION.headers, "x-csrf-token": "short" }, { bp_session: SESSION, bp_csrf: "short" }],
    ["the session token offered as the CSRF token", { ...MUTATION.headers, "x-csrf-token": SESSION }, { bp_session: SESSION, bp_csrf: CSRF }],
  ])("refuses a mutation with %s, without calling the backend", async (_name, headers, cookies) => {
    const response = await call("POST", { headers, cookies, body: "{}" });
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: "CSRF validation failed" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers an unauthenticated mutation 401 (the origin is right, there is just no session)", async () => {
    expect((await call("POST", { ...MUTATION, cookies: null })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not ask for Origin or a CSRF token on a read", async () => {
    expect((await call("GET", { cookies: { bp_session: SESSION } })).status).toBe(200);
    expect(sentHeaders().has("x-csrf-token")).toBe(false);
  });

  it("keeps validating If-Match exactly as before", async () => {
    expect((await call("POST", { ...MUTATION, headers: { ...MUTATION.headers, "if-match": "abc" } })).status).toBe(400);
    await call("POST", { ...MUTATION, headers: { ...MUTATION.headers, "if-match": '"3"' } });
    expect(sentHeaders().get("if-match")).toBe('"3"');
  });

  it("over https uses the __Host- cookies and the https origin", async () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("BACKEND_URL", "http://backend:8000"); // production accepts only a private backend address
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    const cookies = { "__Host-bp_session": SESSION, "__Host-bp_csrf": CSRF };
    const headers = { ...MUTATION.headers, origin: "https://app.example.com" };

    expect((await call("POST", { headers, cookies, body: "{}" })).status).toBe(200);
    expect(sentHeaders().get("authorization")).toBe(`Bearer ${SESSION}`);
    expect((await call("POST", { headers, cookies: SIGNED_IN, body: "{}" })).status).toBe(401); // the plain names are not read
  });
});

describe("what the backend answers", () => {
  it("turns a 401 (expired, revoked, disabled) into a fixed answer that names the login page, never the backend body", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "Not authenticated", leaked: SESSION }), { status: 401, headers: { "content-type": "application/json" } }));

    const response = await call("GET");

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated", login: "/login" });
  });

  it("relays a 403 (role, or a session-bound CSRF refusal) as a 403: it is NOT an authentication failure", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: { code: "csrf_failed", message: "The request could not be verified." } }), { status: 403, headers: { "content-type": "application/json" } }));
    const response = await call("POST", MUTATION);
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ detail: { code: "csrf_failed", message: "The request could not be verified." } });
  });

  it("relays a 404 unchanged (tenant isolation answers are identical)", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "Organization not found" }), { status: 404, headers: { "content-type": "application/json" } }));
    const response = await call("GET", { org: OTHER_ORG });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ detail: "Organization not found" });
  });
});

describe("modes do not leak into each other", () => {
  it("with no mode configured the BFF refuses everything (503) and calls nothing", async () => {
    vi.stubEnv("AUTH_MODE", "");
    expect((await call("GET")).status).toBe(503);
    expect((await call("POST", MUTATION)).status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("an unusable PUBLIC_ORIGIN means no mode: nothing is authenticated and mutations are not accepted", async () => {
    vi.stubEnv("PUBLIC_ORIGIN", "");
    expect((await call("POST", MUTATION)).status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("dev mode still works with the dev cookie and ignores a session cookie", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await call("GET", { cookies: { bp_session: SESSION } })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
    await call("GET", { cookies: { bp_dev_user: "maria@dev.test" } });
    expect(Object.fromEntries(sentHeaders())).toEqual({ accept: "application/json", "x-dev-user-email": "maria@dev.test", "x-organization-id": ORG, "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });
});
