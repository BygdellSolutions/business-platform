// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as accept from "./accept/route";
import * as acceptNew from "./accept-new/route";
import * as preview from "./preview/route";

/**
 * The BFF side of invitations (session mode). Three doors with three different trust rules:
 *   preview and accept-new  BEFORE a session  -> Origin and the pre-auth double submit, checked before FastAPI
 *   accept                  signed in         -> credential from the cookie, Origin, the session CSRF double submit
 * The token is only ever in a POST body; nothing from the browser names an organization, a role or an email.
 */

const ORIGIN = "http://127.0.0.1:3100";
const PRE = "P".repeat(43);
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const NEW_SESSION = "N".repeat(43);
const NEW_CSRF = "M".repeat(43);
const TOKEN = "T".repeat(43);
const ORG = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ORG = "00000000-0000-4000-8000-0000000000b2";

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: unknown = {}) => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  vi.stubEnv("TRUSTED_PROXY_HOPS", "0");
  fetchMock = vi.fn().mockImplementation(() => reply(200, {}));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function post(path: string, options: { headers?: Record<string, string>; cookies?: Record<string, string>; body?: unknown } = {}) {
  const { headers = {}, cookies = {}, body = {} } = options;
  const cookie = Object.entries(cookies).map(([k, v]) => `${k}=${v}`).join("; ");
  return new NextRequest(`${ORIGIN}${path}`, { method: "POST", headers: { "content-type": "application/json", ...(cookie ? { cookie } : {}), ...headers }, body: JSON.stringify(body) });
}

const PRE_AUTH = { headers: { origin: ORIGIN, "x-pre-auth": PRE }, cookies: { bp_pre: PRE } };
const LOGGED_IN = { headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_session: SESSION, bp_csrf: CSRF } };
const upstream = () => fetchMock.mock.calls[0] as [string, { method: string; body: string; headers: Headers }];
const sentHeaders = () => Object.fromEntries(upstream()[1].headers);

describe("preview (before authentication)", () => {
  const request = (extra: Parameters<typeof post>[1] = PRE_AUTH, body: unknown = { token: TOKEN }) => post("/api/invite/preview", { ...extra, body });

  it("forwards only the token in a body, with no credential and no organization, and passes on exactly four fields", async () => {
    fetchMock.mockImplementation(() => reply(200, { organization_name: "Org", email: "a@b.test", role: "viewer", account_exists: false, inviter: "x@y.test", members: [1, 2], legal_name: "Secret AB" }));

    const response = await preview.POST(request({ ...PRE_AUTH, cookies: { ...PRE_AUTH.cookies, bp_session: SESSION }, headers: { ...PRE_AUTH.headers, authorization: "Bearer evil", "x-organization-id": ORG } }));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ organization_name: "Org", email: "a@b.test", role: "viewer", account_exists: false });
    const [url, init] = upstream();
    expect(url).toBe("http://backend.test:8000/api/invite/preview");
    expect(JSON.parse(init.body)).toEqual({ token: TOKEN });
    expect(sentHeaders()).toEqual({ accept: "application/json", "content-type": "application/json", "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) }); // no Authorization, no organization, no pre-auth value
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("refuses a missing or wrong pre-auth pair and a foreign Origin before contacting FastAPI", async () => {
    expect((await preview.POST(request({ headers: { origin: ORIGIN }, cookies: { bp_pre: PRE } }))).status).toBe(403);
    expect((await preview.POST(request({ headers: { origin: ORIGIN, "x-pre-auth": "X".repeat(43) }, cookies: { bp_pre: PRE } }))).status).toBe(403);
    expect((await preview.POST(request({ headers: { origin: "https://evil.example", "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }))).status).toBe(403);
    expect((await preview.POST(request({ headers: { "x-pre-auth": PRE }, cookies: { bp_pre: PRE } }))).status).toBe(403); // no Origin at all
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("gives ONE generic answer for every unusable token, including a malformed one (which never reaches FastAPI)", async () => {
    fetchMock.mockImplementation(() => reply(404, { detail: "Invitation not found" }));
    const answers = [];
    for (const token of [TOKEN, "short", 42, null]) {
      const response = await preview.POST(request(PRE_AUTH, { token }));
      answers.push([response.status, await response.json()]);
    }
    expect(new Set(answers.map((a) => JSON.stringify(a))).size).toBe(1);
    expect(answers[0][0]).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1); // only the well-formed token was sent on
  });

  it("does not exist outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await preview.POST(request())).status).toBe(404);
  });
});

describe("accept as a signed-in account", () => {
  const request = (extra: Parameters<typeof post>[1] = LOGGED_IN, body: unknown = { token: TOKEN }) => post("/api/invite/accept", { ...extra, body });

  it("builds Authorization from the cookie, forwards the validated CSRF token, sends only the token and no organization or role", async () => {
    fetchMock.mockImplementation(() => reply(200, { organization_id: ORG, role: "viewer", joined: true, extra: "dropped" }));

    const response = await accept.POST(request({ ...LOGGED_IN, headers: { ...LOGGED_IN.headers, "x-organization-id": OTHER_ORG, "x-role": "owner", authorization: "Bearer evil" } }, { token: TOKEN, organization_id: OTHER_ORG, role: "owner" }));

    expect(await response.json()).toEqual({ organization_id: ORG, role: "viewer", joined: true });
    expect(JSON.parse(upstream()[1].body)).toEqual({ token: TOKEN });
    expect(sentHeaders()).toEqual({ accept: "application/json", "content-type": "application/json", authorization: `Bearer ${SESSION}`, "x-csrf-token": CSRF, "x-request-id": expect.stringMatching(/^[0-9a-f]{32}$/) });
  });

  it("refuses without the session CSRF pair or with a foreign Origin, before FastAPI", async () => {
    expect((await accept.POST(request({ headers: { origin: ORIGIN }, cookies: LOGGED_IN.cookies }))).status).toBe(403);
    expect((await accept.POST(request({ headers: { origin: ORIGIN, "x-csrf-token": "E".repeat(43) }, cookies: LOGGED_IN.cookies }))).status).toBe(403);
    expect((await accept.POST(request({ headers: { origin: "https://evil.example", "x-csrf-token": CSRF }, cookies: LOGGED_IN.cookies }))).status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("is a 401 naming the login page without a session", async () => {
    const response = await accept.POST(request({ headers: { origin: ORIGIN, "x-csrf-token": CSRF }, cookies: { bp_csrf: CSRF } }));
    expect(response.status).toBe(401);
    expect((await response.json()).login).toBe("/login"); // no invitation token in the login address
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("tells a wrong account apart from an unusable invitation, and nothing else", async () => {
    fetchMock.mockImplementationOnce(() => reply(403, { detail: { code: "invitation_wrong_account", message: "x" } }));
    const wrong = await accept.POST(request());
    expect(wrong.status).toBe(403);
    expect((await wrong.json()).detail.code).toBe("invitation_wrong_account");

    fetchMock.mockImplementationOnce(() => reply(404, { detail: "Invitation not found" }));
    const unusable = await accept.POST(request());
    expect(unusable.status).toBe(404);
    expect((await unusable.json()).detail.code).toBe("invitation_unusable");
  });
});

describe("create an account (before authentication)", () => {
  const BODY = { token: TOKEN, name: "Nina", password: "a brand new long passphrase" };
  const request = (extra: Parameters<typeof post>[1] = PRE_AUTH, body: unknown = BODY) => post("/api/invite/accept-new", { ...extra, body });
  const created = { token: NEW_SESSION, csrf_token: NEW_CSRF, expires_at: "2026-10-11T12:00:00Z", organization_id: ORG, role: "viewer", user: { id: "u", email: "n@b.test", name: "Nina", can_create_organizations: false } };

  it("sets the protected cookies, answers only where to go, and never returns the session token", async () => {
    fetchMock.mockImplementation(() => reply(200, created));

    const response = await acceptNew.POST(request());

    expect(response.status).toBe(200);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ next: `/o/${ORG}` });
    expect(text).not.toContain(NEW_SESSION);
    expect(text).not.toContain(NEW_CSRF);
    const cookies = response.headers.getSetCookie().join("\n");
    expect(cookies).toMatch(new RegExp(`bp_session=${NEW_SESSION}[^\\n]*HttpOnly`, "i"));
    expect(cookies).toContain(`bp_csrf=${NEW_CSRF}`);
    expect(JSON.parse(upstream()[1].body)).toEqual(BODY);
    expect(sentHeaders().authorization).toBeUndefined();
  });

  it("carries only the token, a name and a password: an email, organization or role from the browser is not forwarded", async () => {
    fetchMock.mockImplementation(() => reply(200, created));
    await acceptNew.POST(request(PRE_AUTH, { ...BODY, email: "other@b.test", organization_id: OTHER_ORG, role: "owner" }));
    expect(Object.keys(JSON.parse(upstream()[1].body)).sort()).toEqual(["name", "password", "token"]);
  });

  it("refuses without the pre-auth pair before FastAPI, and sets no cookie", async () => {
    const response = await acceptNew.POST(request({ headers: { origin: ORIGIN }, cookies: {} }));
    expect(response.status).toBe(403);
    expect(response.headers.getSetCookie()).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("maps the backend answers: weak password with its wording, existing account, unusable token", async () => {
    fetchMock.mockImplementationOnce(() => reply(422, { detail: { code: "password_policy", message: "The password must be at least 12 characters." } }));
    const weak = await acceptNew.POST(request());
    expect([weak.status, (await weak.json()).detail.message]).toEqual([422, "The password must be at least 12 characters."]);

    fetchMock.mockImplementationOnce(() => reply(409, { detail: { code: "account_exists" } }));
    expect((await acceptNew.POST(request())).status).toBe(409);

    fetchMock.mockImplementationOnce(() => reply(404, { detail: "Invitation not found" }));
    const gone = await acceptNew.POST(request());
    expect(gone.status).toBe(404);
    expect(gone.headers.getSetCookie()).toEqual([]);
  });

  it("does not set a session from an answer it cannot trust", async () => {
    fetchMock.mockImplementation(() => reply(200, { ...created, organization_id: "not-a-uuid" }));
    const response = await acceptNew.POST(request());
    expect(response.status).toBe(502);
    expect(response.headers.getSetCookie()).toEqual([]);
  });
});
