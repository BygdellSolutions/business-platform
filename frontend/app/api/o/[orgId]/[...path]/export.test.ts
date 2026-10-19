// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as route from "./route";

/** The complete copy of an organization's data through the BFF: POST only, streamed, validated headers, a long allowance. */

const ORG = "00000000-0000-4000-8000-0000000000a1";
const ORIGIN = "http://localhost:3100";
const PATH = ["organization", "export"];

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "dev");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const zip = (body: BodyInit | null = "PK", headers: Record<string, string> = {}) =>
  new Response(body, { status: 200, headers: { "content-type": "application/zip", "content-disposition": 'attachment; filename="export-acme-2026-10-15.zip"', ...headers } });

function call(method: "GET" | "POST", body: string | undefined = JSON.stringify({ password: "secret" })) {
  const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/${PATH.join("/")}`, {
    method,
    headers: { cookie: "bp_dev_user=maria@dev.test", "content-type": "application/json", origin: ORIGIN, host: "localhost:3100" },
    body: method === "GET" ? undefined : body,
  });
  return route[method](request, { params: Promise.resolve({ orgId: ORG, path: PATH }) });
}

describe("the export through the BFF", () => {
  it("sends the password on, asks for a ZIP, and streams it back with rebuilt headers", async () => {
    fetchMock.mockResolvedValue(zip("PKdata", { "set-cookie": "x=y" }));

    const response = await call("POST");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/zip");
    expect(response.headers.get("content-disposition")).toBe('attachment; filename="export-acme-2026-10-15.zip"');
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(await response.text()).toBe("PKdata");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers }];
    expect(url).toBe("http://backend.test:8000/api/organization/export");
    expect(JSON.parse(init.body as string)).toEqual({ password: "secret" });
    expect(init.headers.get("accept")).toContain("application/zip");
    expect(init.headers.get("x-organization-id")).toBe(ORG);
  });

  it("allows the exchange as long as a large copy needs, while an ordinary call keeps the short limit", async () => {
    const timeout = vi.spyOn(AbortSignal, "timeout");
    fetchMock.mockResolvedValue(zip());
    await call("POST");

    expect(timeout).toHaveBeenCalledWith(20 * 60 * 1000);

    timeout.mockClear();
    fetchMock.mockResolvedValue(new Response("[]", { status: 200, headers: { "content-type": "application/json" } }));
    await route.GET(new NextRequest(`${ORIGIN}/api/o/${ORG}/customers`, { headers: { cookie: "bp_dev_user=maria@dev.test" } }), { params: Promise.resolve({ orgId: ORG, path: ["customers"] }) });
    expect(timeout).toHaveBeenCalledWith(15_000);
  });

  it("is POST only: a GET of the same address is a 404 and never reaches the backend", async () => {
    expect((await call("GET")).status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [403, { detail: { code: "reauthentication_failed" } }],
    [409, { detail: { code: "export_required" } }],
    [429, { detail: { code: "rate_limited" } }],
  ])("relays a %s as JSON, never as a download", async (status, body) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "retry-after": "60" } }));

    const response = await call("POST");

    expect(response.status).toBe(status);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(response.headers.get("content-disposition")).toBeNull();
    expect(await response.json()).toEqual(body);
  });

  it.each([
    ["a JSON body that arrives as a 200", () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })],
    ["a PDF", () => new Response("%PDF-", { status: 200, headers: { "content-type": "application/pdf", "content-disposition": "attachment" } })],
  ])("is a 502 for %s, and none of its bytes pass", async (_name, make) => {
    fetchMock.mockResolvedValue(make());

    const response = await call("POST");

    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("%PDF");
  });

  it("a ZIP that arrives anywhere else is never relayed as text", async () => {
    fetchMock.mockResolvedValue(zip());

    const response = await route.GET(new NextRequest(`${ORIGIN}/api/o/${ORG}/customers`, { headers: { cookie: "bp_dev_user=maria@dev.test" } }), { params: Promise.resolve({ orgId: ORG, path: ["customers"] }) });

    expect(response.status).toBe(502);
  });
});
