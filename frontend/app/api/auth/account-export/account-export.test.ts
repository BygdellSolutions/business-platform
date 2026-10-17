// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as route from "./route";

const ORIGIN = "http://127.0.0.1:3100";
const SESSION = "S".repeat(43);

let fetchMock: ReturnType<typeof vi.fn>;
const reply = (status: number, body: string, headers: Record<string, string> = {}) => new Response(body, { status, headers: { "content-type": "application/json", ...headers } });
const call = (cookies = `bp_session=${SESSION}`) => route.GET(new NextRequest(`${ORIGIN}/api/auth/account-export`, { headers: cookies ? { cookie: cookies } : {} }));

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

describe("GET /api/auth/account-export", () => {
  it("passes the copy on as a JSON download with rebuilt headers", async () => {
    const copy = JSON.stringify({ profile: { email: "ada@example.test" }, memberships: [] });
    fetchMock.mockResolvedValue(reply(200, copy, { "set-cookie": "x=y", "x-secret": "no" }));

    const response = await call();

    expect(response.status).toBe(200);
    expect(await response.text()).toBe(copy);
    expect(response.headers.get("content-disposition")).toBe('attachment; filename="account-data.json"');
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(response.headers.get("x-secret")).toBeNull();
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("http://backend.test:8000/api/me/export");
  });

  it("needs a session", async () => {
    expect((await call("")).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["something that is not JSON", reply(200, "<html>")],
    ["a JSON list", reply(200, "[]")],
    ["an object that is not an account copy", reply(200, '{"other": 1}')],
    ["an enormous answer", reply(200, JSON.stringify({ profile: {}, filler: "x".repeat(5 * 1024 * 1024 + 10) }))],
    ["a server error", reply(500, "{}")],
  ])("is a 502 for %s and none of it reaches the browser", async (_name, upstream) => {
    fetchMock.mockResolvedValue(upstream);

    const response = await call();

    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("filler");
  });

  it("says plainly when too many copies were asked for", async () => {
    fetchMock.mockResolvedValue(reply(429, "{}", { "retry-after": "3600" }));

    expect((await call()).status).toBe(429);
  });

  it("is a 404 outside session mode", async () => {
    vi.stubEnv("AUTH_MODE", "dev");

    expect((await call()).status).toBe(404);
  });
});
