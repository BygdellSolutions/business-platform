// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as login from "./login/route";
import * as logout from "./logout/route";
import * as pre from "./pre/route";
import * as setup from "./setup/route";

/** The BFF side of login, setup and logout (session mode): Origin and pre-auth/CSRF checks BEFORE FastAPI, cookies, errors. */

const ORIGIN = "http://127.0.0.1:3100";
const PRE = "P".repeat(43);
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const NEW_SESSION = "N".repeat(43);
const NEW_CSRF = "M".repeat(43);
const LINK = "L".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";

let fetchMock: ReturnType<typeof vi.fn>;

const upstreamSession = () => ({ token: NEW_SESSION, csrf_token: NEW_CSRF, expires_at: "2026-10-11T12:00:00Z", user: { id: "u", email: "a@b.test", name: "A", can_create_organizations: false } });
const jsonResponse = (status: number, body: unknown, headers: Record<string, string> = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  vi.stubEnv("TRUSTED_PROXY_HOPS", "0");
  fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, upstreamSession()));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const preRequest = () => new NextRequest(`${ORIGIN}/api/auth/pre`);

function post(path: string, options: { headers?: Record<string, string>; cookies?: Record<string, string>; body?: unknown; rawBody?: string } = {}) {
  const { headers = {}, cookies = {}, body, rawBody } = options;
  const cookie = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
  return new NextRequest(`${ORIGIN}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...(cookie ? { cookie } : {}), ...headers },
    body: rawBody ?? JSON.stringify(body ?? {}),
  });
}

const PRE_AUTH = { headers: { origin: ORIGIN, "x-pre-auth": PRE }, cookies: { bp_pre: PRE } };
const loginRequest = (body: unknown = { email: "a@b.test", password: "correct horse battery" }, extra: Parameters<typeof post>[1] = PRE_AUTH) => post("/api/auth/login", { ...extra, body });
const setupRequest = (body: unknown = { token: LINK, password: "a brand new long passphrase" }, extra: Parameters<typeof post>[1] = PRE_AUTH) => post("/api/auth/setup", { ...extra, body });
const LOGGED_IN = { headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_session: SESSION, bp_csrf: CSRF } };

function setCookies(response: Response): string[] {
  return response.headers.getSetCookie();
}

describe("GET /api/auth/pre", () => {
  it("issues a fresh random pre-auth secret as an HttpOnly cookie and in the body, uncached", async () => {
    const first = await pre.GET(preRequest());
    const second = await pre.GET(preRequest());

    const a = ((await first.json()) as { token: string }).token;
    const b = ((await second.json()) as { token: string }).token;
    expect(a).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(a).not.toBe(b);
    const cookie = setCookies(first)[0];
    expect(cookie).toContain(`bp_pre=${a}`);
    expect(cookie).toMatch(/HttpOnly/i);
    expect(cookie).toMatch(/SameSite=lax/i);
    expect(cookie).toMatch(/Path=\//);
    expect(cookie).not.toMatch(/Domain=/i);
    expect(cookie).not.toMatch(/Secure/i); // plain http in development
    expect(first.headers.get("cache-control")).toBe("no-store");
  });

  it("uses the __Host- name and Secure over https", async () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    const cookie = (await pre.GET(preRequest())).headers.getSetCookie()[0];
    expect(cookie).toMatch(/^__Host-bp_pre=/);
    expect(cookie).toMatch(/Secure/i);
    expect(cookie).toMatch(/Path=\//);
    expect(cookie).not.toMatch(/Domain=/i);
  });

  it("does not exist outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await pre.GET(preRequest())).status).toBe(404);
  });
});

describe("POST /api/auth/login", () => {
  it("passes the credentials to FastAPI and sets the protected cookies; the browser gets a destination and never a token", async () => {
    const response = await login.POST(loginRequest());

    expect(response.status).toBe(200);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ next: "/" });
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string; body: string; headers: Headers }];
    expect(url).toBe("http://backend.test:8000/api/auth/login");
    expect(JSON.parse(init.body)).toEqual({ email: "a@b.test", password: "correct horse battery" });
    expect(init.headers.has("authorization")).toBe(false);
    expect(init.headers.has("cookie")).toBe(false);
    expect(JSON.stringify([...response.headers].filter(([name]) => name !== "set-cookie" && name !== "x-middleware-set-cookie"))).not.toContain(NEW_SESSION); // only the cookie carries it (the second name is an in-process artifact of NextResponse; the real server sends only set-cookie, which a Playwright spec checks)
    expect(text).not.toContain(NEW_SESSION);
    expect(text).not.toContain(NEW_CSRF);
  });

  it("sets the session cookie HttpOnly and the CSRF cookie readable, both SameSite=Lax, Path=/, no Domain, with the session's expiry", async () => {
    const response = await login.POST(loginRequest());
    const cookies = setCookies(response);
    const session = cookies.find((c) => c.startsWith("bp_session="))!;
    const csrf = cookies.find((c) => c.startsWith("bp_csrf="))!;

    expect(session).toContain(`bp_session=${NEW_SESSION}`);
    expect(session).toMatch(/HttpOnly/i);
    expect(csrf).toContain(`bp_csrf=${NEW_CSRF}`);
    expect(csrf).not.toMatch(/HttpOnly/i); // the page must read it to echo it
    for (const cookie of [session, csrf]) {
      expect(cookie).toMatch(/SameSite=lax/i);
      expect(cookie).toMatch(/Path=\//);
      expect(cookie).not.toMatch(/Domain=/i);
      expect(cookie).toMatch(/Expires=Sun, 11 Oct 2026 12:00:00 GMT/);
    }
    expect(cookies.some((c) => c.startsWith("bp_pre=") && /Max-Age=0/i.test(c))).toBe(true); // the pre-auth secret is consumed
  });

  it("over https the cookies are __Host- prefixed and Secure", async () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("BACKEND_URL", "http://backend:8000"); // production accepts only a private backend address
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    const request = loginRequest(undefined, { headers: { origin: "https://app.example.com", "x-pre-auth": PRE }, cookies: { "__Host-bp_pre": PRE } });

    const cookies = setCookies(await login.POST(request));

    const session = cookies.find((c) => c.startsWith("__Host-bp_session="))!;
    const csrf = cookies.find((c) => c.startsWith("__Host-bp_csrf="))!;
    expect(session).toMatch(/HttpOnly/i);
    for (const cookie of [session, csrf]) {
      expect(cookie).toMatch(/Secure/i);
      expect(cookie).toMatch(/Path=\//);
      expect(cookie).not.toMatch(/Domain=/i);
    }
  });

  it("passes the browser's current session cookie upstream (only so FastAPI can end it), never a client header", async () => {
    await login.POST(loginRequest(undefined, { ...PRE_AUTH, headers: { ...PRE_AUTH.headers, authorization: "Bearer EVIL" }, cookies: { ...PRE_AUTH.cookies, bp_session: SESSION } }));
    const init = fetchMock.mock.calls[0][1] as { headers: Headers };
    expect(init.headers.get("authorization")).toBe(`Bearer ${SESSION}`);
  });

  it.each<[string, Parameters<typeof post>[1]]>([
    ["no Origin", { headers: { "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }],
    ["a foreign Origin", { headers: { origin: "http://evil.example", "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }],
    ["no pre-auth header", { headers: { origin: ORIGIN }, cookies: { bp_pre: PRE } }],
    ["no pre-auth cookie", { headers: { origin: ORIGIN, "x-pre-auth": PRE }, cookies: {} }],
    ["a pre-auth header that differs from the cookie", { headers: { origin: ORIGIN, "x-pre-auth": "X".repeat(43) }, cookies: { bp_pre: PRE } }],
    ["a malformed pre-auth pair", { headers: { origin: ORIGIN, "x-pre-auth": "x" }, cookies: { bp_pre: "x" } }],
    ["a CSRF cookie in place of the pre-auth cookie", { headers: { origin: ORIGIN, "x-pre-auth": PRE }, cookies: { bp_csrf: PRE } }],
  ])("refuses %s before contacting FastAPI", async (_name, extra) => {
    const response = await login.POST(loginRequest(undefined, extra));
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(setCookies(response)).toEqual([]);
  });

  it("answers every wrong credential with the one generic 401 and sets no cookie", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "Invalid email or password" }));
    const response = await login.POST(loginRequest());
    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Invalid email or password" });
    expect(setCookies(response)).toEqual([]);
  });

  it("never relays the backend's own text for a failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "No such user ghost@example.com", internal: SESSION }));
    const body = await (await login.POST(loginRequest())).text();
    expect(body).not.toContain("ghost");
    expect(body).not.toContain(SESSION);
  });

  it("maps throttling and busy to their own safe answers, keeping Retry-After", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(429, { detail: { code: "throttled", message: "x" } }, { "retry-after": "900" }));
    const throttled = await login.POST(loginRequest());
    expect(throttled.status).toBe(429);
    expect(throttled.headers.get("retry-after")).toBe("900");
    fetchMock.mockResolvedValueOnce(jsonResponse(503, { detail: { code: "auth_busy" } }, { "retry-after": "1" }));
    expect((await login.POST(loginRequest())).status).toBe(503);
  });

  it.each([500, 502, 404, 418])("treats an unexpected %i as unavailable (502), with no backend detail", async (status) => {
    fetchMock.mockResolvedValue(jsonResponse(status, { detail: "Traceback: secret" }));
    const response = await login.POST(loginRequest());
    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("secret");
  });

  it("an unreachable backend is unavailable, not an error page", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect((await login.POST(loginRequest())).status).toBe(502);
  });

  it("does not trust a 200 that is not a session", async () => {
    for (const body of [{}, { token: "short", csrf_token: NEW_CSRF, expires_at: "2026-10-11T12:00:00Z" }, { token: NEW_SESSION, csrf_token: NEW_CSRF, expires_at: "garbage" }, null]) {
      fetchMock.mockResolvedValueOnce(jsonResponse(200, body));
      const response = await login.POST(loginRequest());
      expect(response.status).toBe(502);
      expect(setCookies(response)).toEqual([]);
    }
  });

  it.each([{}, { email: "a@b.test" }, { password: "x" }, { email: 1, password: "x" }, { email: "x".repeat(321), password: "x" }, { email: "a@b.test", password: "x".repeat(4097) }])("refuses the malformed body %j", async (body) => {
    expect((await login.POST(loginRequest(body))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a body that is not JSON", async () => {
    expect((await login.POST(post("/api/auth/login", { ...PRE_AUTH, rawBody: "email=a&password=b", headers: { ...PRE_AUTH.headers, "content-type": "application/x-www-form-urlencoded" } }))).status).toBe(400);
    expect((await login.POST(post("/api/auth/login", { ...PRE_AUTH, rawBody: "[1,2]" }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  describe("the destination after login is validated (no open redirect)", () => {
    it.each([
      [`/o/${ORG}/customers`, `/o/${ORG}/customers`],
      [`/o/${ORG}`, `/o/${ORG}`],
      ["/", "/"],
    ])("keeps %j", async (next, expected) => {
      expect(await (await login.POST(loginRequest({ email: "a@b.test", password: "x", next }))).json()).toEqual({ next: expected });
    });

    it.each(["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/login", "/api/auth/logout", `/o/${ORG}/%2e%2e/x`, undefined, 42])("replaces %j with /", async (next) => {
      expect(await (await login.POST(loginRequest({ email: "a@b.test", password: "x", next }))).json()).toEqual({ next: "/" });
    });
  });

  it("forwards no client address with no trusted proxy, and the proxy-established one otherwise", async () => {
    await login.POST(loginRequest(undefined, { ...PRE_AUTH, headers: { ...PRE_AUTH.headers, "x-forwarded-for": "6.6.6.6", "x-client-ip": "6.6.6.6" } }));
    expect((fetchMock.mock.calls[0][1] as { headers: Headers }).headers.has("x-client-ip")).toBe(false);

    vi.stubEnv("TRUSTED_PROXY_HOPS", "1");
    await login.POST(loginRequest(undefined, { ...PRE_AUTH, headers: { ...PRE_AUTH.headers, "x-forwarded-for": "6.6.6.6, 198.51.100.9", "x-client-ip": "6.6.6.6" } }));
    expect((fetchMock.mock.calls[1][1] as { headers: Headers }).headers.get("x-client-ip")).toBe("198.51.100.9");
  });

  it("does not exist outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await login.POST(loginRequest())).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("POST /api/auth/setup", () => {
  it("redeems the link and signs in like a login (cookies set, destination /, no token in the response)", async () => {
    const response = await setup.POST(setupRequest());

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ next: "/" });
    expect(setCookies(response).some((c) => c.startsWith(`bp_session=${NEW_SESSION}`) && /HttpOnly/i.test(c))).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, { body: string }];
    expect(url).toBe("http://backend.test:8000/api/auth/setup");
    expect(JSON.parse(init.body)).toEqual({ token: LINK, password: "a brand new long passphrase" });
    expect(url).not.toContain(LINK); // the link secret travels in the body only, never in a URL
  });

  it("needs Origin and the pre-auth pair, before FastAPI", async () => {
    const attempts: Parameters<typeof post>[1][] = [{ headers: { "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }, { headers: { origin: ORIGIN }, cookies: {} }, { headers: { origin: "http://evil.example", "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }];
    for (const extra of attempts) {
      expect((await setup.POST(setupRequest(undefined, extra))).status).toBe(403);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("answers an invalid, used or expired link with one generic message, and never calls FastAPI for a malformed one", async () => {
    fetchMock.mockResolvedValue(jsonResponse(400, { detail: { code: "invalid_setup_link", message: "used" } }));
    const response = await setup.POST(setupRequest());
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: { code: "invalid_setup_link", message: "This link is invalid or has expired." } });

    fetchMock.mockClear();
    for (const token of ["short", "x".repeat(44), "../".repeat(15)]) {
      expect((await setup.POST(setupRequest({ token, password: "a brand new long passphrase" }))).status).toBe(400);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows the backend's password-policy wording, and nothing else of its text", async () => {
    fetchMock.mockResolvedValue(jsonResponse(422, { detail: { code: "password_policy", message: "The password must be at least 12 characters." } }));
    const response = await setup.POST(setupRequest());
    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({ detail: { code: "password_policy", message: "The password must be at least 12 characters." } });

    fetchMock.mockResolvedValue(jsonResponse(422, { detail: { message: "x".repeat(500) } }));
    expect(((await (await setup.POST(setupRequest())).json()) as { detail: { message: string } }).detail.message).toBe("That password is not acceptable.");
  });

  it("maps throttling, busy and failures safely", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(429, {}, { "retry-after": "900" }));
    expect((await setup.POST(setupRequest())).status).toBe(429);
    fetchMock.mockResolvedValueOnce(jsonResponse(503, {}));
    expect((await setup.POST(setupRequest())).status).toBe(503);
    fetchMock.mockResolvedValueOnce(jsonResponse(500, { detail: "secret" }));
    const failed = await setup.POST(setupRequest());
    expect(failed.status).toBe(502);
    expect(await failed.text()).not.toContain("secret");
  });

  it("does not exist outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await setup.POST(setupRequest())).status).toBe(404);
  });
});

describe("POST /api/auth/logout", () => {
  it("revokes the session at FastAPI with the cookie's token and the validated CSRF token, then clears both cookies with the same attributes", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const response = await logout.POST(post("/api/auth/logout", LOGGED_IN));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ revoked: "confirmed" });
    const [url, init] = fetchMock.mock.calls[0] as [string, { method: string; headers: Headers }];
    expect(url).toBe("http://backend.test:8000/api/auth/logout");
    expect(init.method).toBe("POST");
    expect(init.headers.get("authorization")).toBe(`Bearer ${SESSION}`);
    expect(init.headers.get("x-csrf-token")).toBe(CSRF);
    const cookies = setCookies(response);
    for (const name of ["bp_session", "bp_csrf"]) {
      const cleared = cookies.find((c) => c.startsWith(`${name}=;`) || c.startsWith(`${name}=`))!;
      expect(cleared).toMatch(/Max-Age=0/i);
      expect(cleared).toMatch(/Path=\//);
      expect(cleared).toMatch(/SameSite=lax/i);
    }
    expect(cookies.find((c) => c.startsWith("bp_session="))).toMatch(/HttpOnly/i);
  });

  it("over https the cookies are cleared under their __Host- names, Secure, Path=/ and without Domain (otherwise the browser ignores the deletion)", async () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("PUBLIC_ORIGIN", "https://app.example.com");
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    const request = post("/api/auth/logout", { headers: { origin: "https://app.example.com", "x-csrf-token": CSRF }, cookies: { "__Host-bp_session": SESSION, "__Host-bp_csrf": CSRF } });

    const cookies = setCookies(await logout.POST(request));

    for (const name of ["__Host-bp_session", "__Host-bp_csrf"]) {
      const cleared = cookies.find((c) => c.startsWith(`${name}=`))!;
      expect(cleared).toMatch(/Max-Age=0/i);
      expect(cleared).toMatch(/Secure/i);
      expect(cleared).toMatch(/Path=\//);
      expect(cleared).not.toMatch(/Domain=/i);
    }
  });

  it.each([
    ["no Origin", { headers: { "x-csrf-token": CSRF }, cookies: { bp_session: SESSION, bp_csrf: CSRF } }],
    ["a foreign Origin", { headers: { origin: "http://evil.example", "x-csrf-token": CSRF }, cookies: { bp_session: SESSION, bp_csrf: CSRF } }],
    ["no CSRF header", { headers: { origin: ORIGIN }, cookies: { bp_session: SESSION, bp_csrf: CSRF } }],
    ["a wrong CSRF header", { headers: { origin: ORIGIN, "x-csrf-token": "X".repeat(43) }, cookies: { bp_session: SESSION, bp_csrf: CSRF } }],
    ["no CSRF cookie", { headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_session: SESSION } }],
  ])("is a CSRF-protected state change: %s is refused and nothing happens (cookies kept, backend not called)", async (_name, extra) => {
    const response = await logout.POST(post("/api/auth/logout", extra));
    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(setCookies(response)).toEqual([]);
  });

  it("an upstream 401 means the session could not be used anyway; the browser is signed out", async () => {
    fetchMock.mockResolvedValue(jsonResponse(401, { detail: "Not authenticated" }));
    const response = await logout.POST(post("/api/auth/logout", LOGGED_IN));
    expect(await response.json()).toEqual({ revoked: "already_invalid" });
    expect(setCookies(response).length).toBe(2);
  });

  it.each([
    ["FastAPI unreachable", () => fetchMock.mockRejectedValue(new TypeError("fetch failed"))],
    ["FastAPI answering 500", () => fetchMock.mockResolvedValue(jsonResponse(500, {}))],
    ["FastAPI answering 403", () => fetchMock.mockResolvedValue(jsonResponse(403, {}))],
    ["FastAPI answering 503", () => fetchMock.mockResolvedValue(jsonResponse(503, {}))],
  ])("clears this browser's cookies but does NOT claim the server revoked the session (%s)", async (_name, arrange) => {
    arrange();
    const response = await logout.POST(post("/api/auth/logout", LOGGED_IN));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ revoked: "unconfirmed" });
    expect(setCookies(response).length).toBe(2);
  });

  it("with no session cookie it still clears and reports nothing to revoke", async () => {
    const response = await logout.POST(post("/api/auth/logout", { headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_csrf: CSRF } }));
    expect(await response.json()).toEqual({ revoked: "already_invalid" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not exist outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await logout.POST(post("/api/auth/logout", LOGGED_IN))).status).toBe(404);
  });
});

describe("every authentication response is uncached", () => {
  it("login, setup and logout answer no-store", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    for (const response of [await login.POST(loginRequest()), await setup.POST(setupRequest()), await logout.POST(post("/api/auth/logout", LOGGED_IN)), await login.POST(loginRequest(undefined, {}))]) {
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
  });
});
