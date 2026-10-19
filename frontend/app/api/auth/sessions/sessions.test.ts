// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as one from "./[id]/route";
import * as endAll from "./end-all/route";
import * as endOthers from "./end-others/route";
import * as list from "./route";

/** The BFF side of "where I am signed in": Origin and CSRF before FastAPI for a change, a fixed set of answers, cookies. */

const ORIGIN = "http://127.0.0.1:3100";
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const ID = "11111111-1111-4111-8111-111111111111";
const ROW = { id: ID, created_at: "2026-10-15T08:00:00Z", last_used_at: "2026-10-15T09:30:00Z", user_agent: "Chrome/120", current: true };

type Options = { cookies?: Record<string, string>; headers?: Record<string, string> };
const AUTH: Options = { cookies: { bp_session: SESSION, bp_csrf: CSRF }, headers: { origin: ORIGIN, "x-csrf-token": CSRF } };
const READ: Options = { cookies: { bp_session: SESSION } };

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body?: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

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

function req(method: string, path: string, options: Options = {}) {
  const { cookies = {}, headers = {} } = options;
  const cookie = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  return new NextRequest(`${ORIGIN}${path}`, { method, headers: { ...(cookie ? { cookie } : {}), ...headers } });
}
const upstreamCall = () => fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers }];
const deleteOne = (options: Options, id = ID) => one.DELETE(req("DELETE", `/api/auth/sessions/${id}`, options), { params: Promise.resolve({ id }) });

describe("GET /api/auth/sessions", () => {
  it("returns the person's sessions reduced to the fields they need", async () => {
    fetchMock.mockResolvedValue(reply(200, [{ ...ROW, token_hash: "never", source: "10.0.0.9" }]));
    const response = await list.GET(req("GET", "/api/auth/sessions", READ));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      sessions: [{ id: ID, createdAt: ROW.created_at, lastUsedAt: ROW.last_used_at, userAgent: "Chrome/120", current: true }],
    });
    expect(upstreamCall()[0]).toContain("/api/auth/sessions");
  });

  it("needs a session and says so plainly", async () => {
    expect((await list.GET(req("GET", "/api/auth/sessions"))).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [200, { not: "a list" }],
    [500, undefined],
    [403, undefined],
  ])("answers 502 when FastAPI says %s", async (status, body) => {
    fetchMock.mockResolvedValue(reply(status, body));
    expect((await list.GET(req("GET", "/api/auth/sessions", READ))).status).toBe(502);
  });

  it("is a 404 outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");
    expect((await list.GET(req("GET", "/api/auth/sessions", READ))).status).toBe(404);
  });
});

describe("changes: Origin and CSRF come first", () => {
  const run = (name: string, options: Options) =>
    name === "end one"
      ? deleteOne(options)
      : name === "end others"
        ? endOthers.POST(req("POST", "/api/auth/sessions/end-others", options))
        : endAll.POST(req("POST", "/api/auth/sessions/end-all", options));

  it.each(["end one", "end others", "end all"])("%s refuses a foreign Origin, a missing CSRF token and a missing session before calling FastAPI", async (name) => {
    expect((await run(name, { cookies: AUTH.cookies, headers: { origin: "https://evil.example", "x-csrf-token": CSRF } })).status).toBe(403);
    expect((await run(name, { cookies: AUTH.cookies, headers: { origin: ORIGIN } })).status).toBe(403);
    expect((await run(name, { cookies: { bp_csrf: CSRF }, headers: AUTH.headers })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("DELETE /api/auth/sessions/{id}", () => {
  it("ends one session and forwards the CSRF token", async () => {
    fetchMock.mockResolvedValue(reply(204));
    const response = await deleteOne(AUTH);

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ended: 1 });
    const [url, init] = upstreamCall();
    expect(url).toContain(`/api/auth/sessions/${ID}`);
    expect(init.method).toBe("DELETE");
    expect(init.headers.get("x-csrf-token")).toBe(CSRF);
  });

  it("never sends a path that is not a UUID to FastAPI", async () => {
    for (const id of ["not-a-uuid", "../../etc", `${ID}/../x`, ""]) {
      expect((await deleteOne(AUTH, id)).status).toBe(404);
    }
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("passes on a 404 for someone else's or an ended session", async () => {
    fetchMock.mockResolvedValue(reply(404, { detail: "Not found" }));
    expect((await deleteOne(AUTH)).status).toBe(404);
  });
});

describe("end others and end all", () => {
  it("end others returns how many ended and does not touch this browser's cookies", async () => {
    fetchMock.mockResolvedValue(reply(200, { ended: 2 }));
    const response = await endOthers.POST(req("POST", "/api/auth/sessions/end-others", AUTH));

    expect(await response.json()).toEqual({ ended: 2 });
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it("end all clears this browser's cookies once FastAPI confirms", async () => {
    fetchMock.mockResolvedValue(reply(200, { ended: 3 }));
    const response = await endAll.POST(req("POST", "/api/auth/sessions/end-all", AUTH));

    expect(await response.json()).toEqual({ ended: 3 });
    const cookies = response.headers.getSetCookie().join(" | ");
    expect(cookies).toContain("bp_session=;");
    expect(cookies).toContain("bp_csrf=;");
  });

  it("end all clears NOTHING when FastAPI could not confirm (the person must not believe they are signed out)", async () => {
    fetchMock.mockResolvedValue(reply(500));
    const response = await endAll.POST(req("POST", "/api/auth/sessions/end-all", AUTH));

    expect(response.status).toBe(502);
    expect(response.headers.getSetCookie()).toEqual([]);
  });

  it("end all also clears the cookies when the session is already invalid (401)", async () => {
    fetchMock.mockResolvedValue(reply(401));
    const response = await endAll.POST(req("POST", "/api/auth/sessions/end-all", AUTH));

    expect(response.status).toBe(401);
    expect(response.headers.getSetCookie().join(" | ")).toContain("bp_session=;");
  });
});
