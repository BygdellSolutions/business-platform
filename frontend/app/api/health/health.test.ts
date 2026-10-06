// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as health from "./route";
import * as ready from "../ready/route";

/**
 * /api/health is LIVENESS (depends on nothing); /api/ready asks the backend and answers ready or unready. Both are
 * public, coarse and uncached: nothing about the database, its revision, the backend's hostname or any error.
 */

const SECRET = "9f3c1a7e5b2d8046c1e7a95b3d20f648a1c7e903d5b6f2a8";
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("PUBLIC_ORIGIN", "http://127.0.0.1:3100");
  vi.stubEnv("BACKEND_URL", "http://secret-backend-host.internal:8000");
  vi.stubEnv("BFF_INTERNAL_SECRET", SECRET);
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(process.stdout, "write").mockImplementation((() => true) as never);
  vi.spyOn(process.stderr, "write").mockImplementation((() => true) as never);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const get = (path: string) => new NextRequest(`http://127.0.0.1:3100${path}`);
const upstream = (status: number, body: unknown) => Promise.resolve(new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

async function dump(response: Response): Promise<string> {
  return (await response.clone().text()) + JSON.stringify(Object.fromEntries(response.headers));
}

describe("GET /api/health: liveness", () => {
  it("answers ok without touching the backend, whatever state the backend is in", async () => {
    fetchMock.mockRejectedValue(new Error("the backend is down"));
    const response = await health.GET(get("/api/health"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("works with no configuration at all (it needs none)", async () => {
    vi.stubEnv("BACKEND_URL", "");
    vi.stubEnv("BFF_INTERNAL_SECRET", "");
    expect((await health.GET(get("/api/health"))).status).toBe(200);
  });
});

describe("GET /api/ready: readiness of the chain", () => {
  it("is ready only when the backend answers 200 with status ready", async () => {
    fetchMock.mockImplementation(() => upstream(200, { status: "ready" }));
    const response = await ready.GET(get("/api/ready"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ready" });
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(fetchMock.mock.calls[0][0]).toBe("http://secret-backend-host.internal:8000/health/ready");
    expect((fetchMock.mock.calls[0][1] as { headers: Headers }).headers.get("x-bff-secret")).toBe(SECRET); // every upstream request carries it
  });

  it.each([
    ["the backend says unready (503)", () => upstream(503, { status: "unready" })],
    ["the backend answers 500", () => upstream(500, "Internal Server Error")],
    ["the backend answers 200 with something else", () => upstream(200, { status: "maybe" })],
    ["the backend answers 200 with garbage", () => upstream(200, "<html>")],
    ["the backend answers 200 with no body", () => Promise.resolve(new Response(null, { status: 200 }))],
    ["the backend redirects", () => Promise.resolve(new Response(null, { status: 302, headers: { location: "http://evil.test/" } }))],
    ["the connection is refused", () => Promise.reject(Object.assign(new TypeError("fetch failed"), { cause: new Error("connect ECONNREFUSED 10.0.0.5:8000") }))],
    ["the request times out", () => Promise.reject(new DOMException("timed out", "TimeoutError"))],
    ["the backend refuses our internal secret", () => Promise.resolve(new Response("{}", { status: 403, headers: { "x-internal-auth": "rejected" } }))],
  ])("is unready, coarsely, when %s", async (_name, answer) => {
    fetchMock.mockImplementation(answer);
    const response = await ready.GET(get("/api/ready"));
    expect(response.status).toBe(503);
    const everything = await dump(response);
    expect(await response.json()).toEqual({ status: "unready" });
    for (const leak of ["secret-backend-host", "ECONNREFUSED", "10.0.0.5", "postgres", "alembic", "revision", "TypeError", "Internal Server Error", SECRET]) {
      expect(everything).not.toContain(leak);
    }
  });

  it("with a database revision in the backend's body, still reveals nothing of it", async () => {
    fetchMock.mockImplementation(() => upstream(503, { status: "unready", revision: "e29c5d7a3b48", database: "postgresql://u:p@db/x" }));
    const response = await ready.GET(get("/api/ready"));
    expect(await dump(response)).not.toMatch(/e29c5d7a3b48|postgresql|database/);
  });

  it("a misconfigured production backend address is unready, never a thrown 500", async () => {
    vi.stubEnv("APP_ENV", "production");
    vi.stubEnv("BACKEND_URL", "https://api.example.com");
    const response = await ready.GET(get("/api/ready"));
    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled(); // and it did not call the public host
  });
});
