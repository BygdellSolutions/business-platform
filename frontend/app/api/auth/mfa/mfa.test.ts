// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as loginMfa from "../login/mfa/route";
import * as login from "../login/route";
import * as action from "./[action]/route";
import * as status from "./route";

/** The BFF side of the second factor: the sign-in steps (pre-auth contract) and the account routes (Origin, CSRF, fixed answers). */

const ORIGIN = "http://127.0.0.1:3100";
const PRE = "P".repeat(43);
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const CHALLENGE = "H".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";
const SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ";
const URI = `otpauth://totp/Business%20Platform%3Aada%40example.test?secret=${SECRET}`;
const CODES = Array.from({ length: 10 }, (_, i) => `${String(i).repeat(8)}-abcdef01`);

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (statusCode: number, body?: unknown, headers: Record<string, string> = {}) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status: statusCode, headers: { "content-type": "application/json", ...headers } });
const upstreamSession = () => ({ token: "N".repeat(43), csrf_token: "M".repeat(43), expires_at: "2026-10-16T12:00:00Z", user: { id: "u", email: "a@b.test", name: "A", can_create_organizations: false } });

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  vi.stubEnv("TRUSTED_PROXY_HOPS", "0");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

type Options = { cookies?: Record<string, string>; headers?: Record<string, string>; body?: unknown };
function req(method: string, path: string, { cookies = {}, headers = {}, body }: Options = {}) {
  const cookie = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
  return new NextRequest(`${ORIGIN}${path}`, { method, headers: { "content-type": "application/json", ...(cookie ? { cookie } : {}), ...headers }, body: body === undefined ? undefined : JSON.stringify(body) });
}
const PRE_AUTH: Options = { headers: { origin: ORIGIN, "x-pre-auth": PRE }, cookies: { bp_pre: PRE } };
const LOGGED_IN: Options = { headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_session: SESSION, bp_csrf: CSRF } };
const upstreamCall = () => fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers; body?: string }];

describe("signing in with a second factor", () => {
  it("a correct password for a person with a second factor returns only the challenge: no cookie, no token", async () => {
    fetchMock.mockResolvedValue(reply(200, { mfa_required: true, challenge: CHALLENGE, expires_at: "2026-10-15T12:05:00Z" }));

    const response = await login.POST(req("POST", "/api/auth/login", { ...PRE_AUTH, body: { email: "a@b.test", password: "correct horse battery" } }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ mfa_required: true, challenge: CHALLENGE });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it("the second step needs Origin and the pre-auth pair before FastAPI is asked", async () => {
    const body = { challenge: CHALLENGE, code: "123456" };

    expect((await loginMfa.POST(req("POST", "/api/auth/login/mfa", { headers: { origin: "https://evil.example", "x-pre-auth": PRE }, cookies: { bp_pre: PRE }, body }))).status).toBe(403);
    expect((await loginMfa.POST(req("POST", "/api/auth/login/mfa", { headers: { origin: ORIGIN }, cookies: { bp_pre: PRE }, body }))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("a right code sets the protected cookies, sends the code and challenge only, and never returns a token", async () => {
    fetchMock.mockResolvedValue(reply(200, upstreamSession()));

    const response = await loginMfa.POST(req("POST", "/api/auth/login/mfa", { ...PRE_AUTH, body: { challenge: CHALLENGE, code: "123456", next: `/o/${ORG}`, email: "ignored@example.test" } }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ next: `/o/${ORG}` });
    expect(response.headers.getSetCookie().join(" | ")).toContain("bp_session=");
    const [url, init] = upstreamCall();
    expect(url).toContain("/api/auth/login/mfa");
    expect(JSON.parse(init.body as string)).toEqual({ challenge: CHALLENGE, code: "123456" });
  });

  it.each([
    ["a challenge of the wrong shape", { challenge: "short", code: "123456" }],
    ["no code", { challenge: CHALLENGE }],
    ["a code that is far too long", { challenge: CHALLENGE, code: "1".repeat(65) }],
  ])("refuses %s before FastAPI is asked", async (_name, body) => {
    expect((await loginMfa.POST(req("POST", "/api/auth/login/mfa", { ...PRE_AUTH, body }))).status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [401, 401],
    [429, 429],
    [503, 503],
    [500, 502],
  ])("answers a FastAPI %s with %s and nothing from FastAPI's text", async (from, to) => {
    fetchMock.mockResolvedValue(reply(from, { detail: "SECRET INTERNALS" }, from === 429 ? { "retry-after": "60" } : {}));

    const response = await loginMfa.POST(req("POST", "/api/auth/login/mfa", { ...PRE_AUTH, body: { challenge: CHALLENGE, code: "123456" } }));

    expect(response.status).toBe(to);
    expect(await response.text()).not.toContain("SECRET INTERNALS");
    expect(response.headers.getSetCookie()).toEqual([]);
  });
});

describe("the account routes", () => {
  it("GET returns the three fields and needs a session", async () => {
    fetchMock.mockResolvedValue(reply(200, { enabled: true, required: false, recovery_codes_left: 9, seed: "never" }));

    const response = await status.GET(req("GET", "/api/auth/mfa", { cookies: { bp_session: SESSION } }));

    expect(await response.json()).toEqual({ status: { enabled: true, required: false, recoveryCodesLeft: 9 } });
    expect((await status.GET(req("GET", "/api/auth/mfa"))).status).toBe(401);
  });

  const post = (name: string, body: unknown, options: Options = LOGGED_IN) => action.POST(req("POST", `/api/auth/mfa/${name}`, { ...options, body }), { params: Promise.resolve({ action: name }) });

  it.each(["enroll", "confirm", "recovery-codes", "disable"])("%s refuses a foreign Origin, a missing CSRF token and a missing session before FastAPI is asked", async (name) => {
    const body = name === "enroll" ? { password: "pw" } : { code: "123456" };

    expect((await post(name, body, { cookies: LOGGED_IN.cookies, headers: { origin: "https://evil.example", "x-csrf-token": CSRF } })).status).toBe(403);
    expect((await post(name, body, { cookies: LOGGED_IN.cookies, headers: { origin: ORIGIN } })).status).toBe(403);
    expect((await post(name, body, { cookies: { bp_csrf: CSRF }, headers: LOGGED_IN.headers })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("only the four known actions exist", async () => {
    for (const name of ["reset", "__proto__", "ENROLL", "status"]) expect((await post(name, { code: "1" })).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("enroll sends the password and returns the key and the link, nothing more", async () => {
    fetchMock.mockResolvedValue(reply(200, { secret: SECRET, uri: URI, seed: "never" }));

    const response = await post("enroll", { password: "my password" });

    expect(await response.json()).toEqual({ enrollment: { secret: SECRET, uri: URI } });
    const [url, init] = upstreamCall();
    expect(url).toContain("/api/auth/mfa/enroll");
    expect(JSON.parse(init.body as string)).toEqual({ password: "my password" });
    expect(init.headers.get("x-csrf-token")).toBe(CSRF);
  });

  it("confirm returns the recovery codes in the one answer that creates them", async () => {
    fetchMock.mockResolvedValue(reply(200, { recovery_codes: CODES }));

    expect(await (await post("confirm", { code: "123456" })).json()).toEqual({ recoveryCodes: CODES });
  });

  it("disable says it is done", async () => {
    fetchMock.mockResolvedValue(reply(204));

    expect(await (await post("disable", { code: "123456" })).json()).toEqual({ disabled: true });
  });

  it.each([
    [400, "invalid_code"],
    [400, "wrong_password"],
    [409, "already_enabled"],
    [409, "required"],
  ])("passes a known refusal (%s %s) on as its code only", async (statusCode, code) => {
    fetchMock.mockResolvedValue(reply(statusCode, { detail: { code, message: "SECRET INTERNALS" } }));

    const response = await post("confirm", { code: "123456" });

    expect(response.status).toBe(statusCode);
    const text = await response.text();
    expect(text).toContain(code);
    expect(text).not.toContain("SECRET INTERNALS");
  });

  it("an unknown refusal becomes a plain 'rejected'", async () => {
    fetchMock.mockResolvedValue(reply(409, { detail: { code: "something_new_and_unreviewed" } }));

    expect(await (await post("confirm", { code: "123456" })).text()).toContain("rejected");
  });

  it("keeps Retry-After on a 429 and refuses an answer that is not what was promised", async () => {
    fetchMock.mockResolvedValueOnce(reply(429, {}, { "retry-after": "60" }));
    expect((await post("confirm", { code: "123456" })).headers.get("retry-after")).toBe("60");

    fetchMock.mockResolvedValueOnce(reply(200, { recovery_codes: ["not-a-code"] }));
    expect((await post("confirm", { code: "123456" })).status).toBe(502);
  });
});
