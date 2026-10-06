// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as catchAll from "@/app/api/o/[orgId]/[...path]/route";
import * as organizations from "@/app/api/organizations/route";
import { classifyFetchError, isInfrastructureFailure } from "@/lib/upstream";

/**
 * The failure contract: when FastAPI cannot do its job the browser gets a FIXED, coarse answer (never a hostname, stack,
 * connection string or raw error), and an ordinary application answer is never mistaken for an infrastructure failure.
 */

const ORIGIN = "http://127.0.0.1:3100";
const ORG = "00000000-0000-4000-8000-0000000000a1";
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const HOST = "db-primary.secret-internal";
const LEAKY = `connect ECONNREFUSED 10.9.8.7:5432 (${HOST}) postgresql://user:hunter2@${HOST}/prod Traceback (most recent call last)`;

let fetchMock: ReturnType<typeof vi.fn>;
let logged: string;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend:8000");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  logged = "";
  vi.spyOn(process.stdout, "write").mockImplementation(((chunk: string) => ((logged += chunk), true)) as never);
  vi.spyOn(process.stderr, "write").mockImplementation(((chunk: string) => ((logged += chunk), true)) as never);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function scoped(method: "GET" | "POST" = "GET") {
  const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/customers`, {
    method,
    headers: { cookie: `bp_session=${SESSION}; bp_csrf=${CSRF}`, ...(method === "POST" ? { origin: ORIGIN, "x-csrf-token": CSRF, "content-type": "application/json" } : {}) },
    body: method === "POST" ? "{}" : undefined,
  });
  return catchAll[method](request, { params: Promise.resolve({ orgId: ORG, path: ["customers"] }) });
}

const reply = (status: number, body: string, headers: Record<string, string> = {}) => Promise.resolve(new Response(body, { status, headers: { "content-type": "application/json", ...headers } }));

describe("thrown fetch failures", () => {
  it.each([
    ["a refused connection", Object.assign(new TypeError("fetch failed"), { cause: new Error(LEAKY) }), 502, "upstream_unavailable"],
    ["a DNS failure", Object.assign(new TypeError("fetch failed"), { cause: Object.assign(new Error(`getaddrinfo ENOTFOUND ${HOST}`), { code: "ENOTFOUND" }) }), 502, "upstream_unavailable"],
    ["a reset", new Error(LEAKY), 502, "upstream_unavailable"],
    ["a timeout", new DOMException(LEAKY, "TimeoutError"), 504, "upstream_timeout"],
    ["an abort", new DOMException(LEAKY, "AbortError"), 504, "upstream_timeout"],
  ])("%s becomes a fixed answer with a stable code and no detail", async (_name, error, status, code) => {
    fetchMock.mockRejectedValue(error);
    const response = await scoped();
    expect(response.status).toBe(status);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ detail: "Backend unavailable", code });
    for (const leak of [HOST, "10.9.8.7", "ECONNREFUSED", "hunter2", "postgresql", "Traceback", "backend:8000"]) {
      expect(text).not.toContain(leak);
      expect(JSON.stringify(Object.fromEntries(response.headers))).not.toContain(leak);
      expect(logged).not.toContain(leak); // the logs name the error CLASS only
    }
    expect(logged).toContain('"event":"upstream_failed"');
  });

  it("classifies by error name only", () => {
    expect(classifyFetchError(new DOMException("x", "TimeoutError"))).toBe("timeout");
    expect(classifyFetchError(new DOMException("x", "AbortError"))).toBe("timeout");
    expect(classifyFetchError(new TypeError("fetch failed"))).toBe("unreachable");
    expect(classifyFetchError("a string")).toBe("unreachable");
  });
});

describe("infrastructure answers from FastAPI", () => {
  it.each([500, 502, 504, 599])("a %i is replaced by a fixed 502 (its body, which can hold a trace, never reaches the browser)", async (status) => {
    fetchMock.mockImplementation(() => reply(status, `{"detail":"${LEAKY}"}`));
    const response = await scoped();
    expect(response.status).toBe(502);
    const text = await response.text();
    expect(JSON.parse(text)).toEqual({ detail: "Backend unavailable", code: "upstream_unavailable" });
    expect(text).not.toContain(HOST);
  });

  it("a 503 stays a 503 with a retry hint, also with a fixed body", async () => {
    fetchMock.mockImplementation(() => reply(503, `{"detail":"${LEAKY}"}`));
    const response = await scoped();
    expect(response.status).toBe(503);
    expect(response.headers.get("retry-after")).toBe("5");
    expect(await response.json()).toEqual({ detail: "Backend unavailable", code: "upstream_unavailable" });
  });

  it("a non-JSON 500 from a crashing backend is the same fixed answer", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response("Internal Server Error", { status: 500, headers: { "content-type": "text/plain" } })));
    expect(await (await scoped()).json()).toEqual({ detail: "Backend unavailable", code: "upstream_unavailable" });
  });

  it("FastAPI refusing the BFF's own secret is OUR misconfiguration: a fixed 502 for the browser, a loud log line for the operator", async () => {
    fetchMock.mockImplementation(() => reply(403, '{"detail":{"code":"internal_auth_failed","message":"Forbidden"}}', { "x-internal-auth": "rejected" }));
    const response = await scoped();
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable", code: "upstream_unavailable" });
    expect(logged).toContain("upstream_refused_internal_secret");
  });
});

describe("ordinary application answers are NOT infrastructure failures", () => {
  it.each([
    [400, { detail: "bad" }],
    [403, { detail: "Your role does not allow this" }], // a role 403 (no internal-auth header) passes through
    [404, { detail: "Not found" }],
    [409, { detail: { code: "stale_record", message: "x" } }],
    [412, { detail: "precondition" }],
    [422, { detail: [{ loc: ["body", "name"], msg: "required", type: "missing" }] }],
    [429, { detail: { code: "throttled" } }],
  ])("a %i passes through with its status and body", async (status, body) => {
    fetchMock.mockImplementation(() => reply(status, JSON.stringify(body)));
    const response = await scoped();
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
  });

  it("only the exact header value marks an internal-auth refusal", () => {
    expect(isInfrastructureFailure(new Response(null, { status: 403 }))).toBe(false);
    expect(isInfrastructureFailure(new Response(null, { status: 403, headers: { "x-internal-auth": "ok" } }))).toBe(false);
    expect(isInfrastructureFailure(new Response(null, { status: 403, headers: { "x-internal-auth": "rejected" } }))).toBe(true);
    expect(isInfrastructureFailure(new Response(null, { status: 499 }))).toBe(false);
    expect(isInfrastructureFailure(new Response(null, { status: 500 }))).toBe(true);
  });
});

describe("the organization-creation door follows the same contract", () => {
  const create = () =>
    organizations.POST(new NextRequest(`${ORIGIN}/api/organizations`, { method: "POST", headers: { cookie: `bp_session=${SESSION}; bp_csrf=${CSRF}`, origin: ORIGIN, "x-csrf-token": CSRF, "content-type": "application/json" }, body: "{}" }));

  it("fixed answers for a thrown failure and for a 5xx; 4xx untouched", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError(LEAKY));
    const refused = await create();
    expect(refused.status).toBe(502);
    expect(await refused.json()).toEqual({ detail: "Backend unavailable", code: "upstream_unavailable" });

    fetchMock.mockImplementationOnce(() => reply(500, LEAKY));
    expect((await create()).status).toBe(502);

    fetchMock.mockImplementationOnce(() => reply(403, '{"detail":{"code":"organization_creation_not_allowed"}}'));
    const forbidden = await create();
    expect(forbidden.status).toBe(403);
    expect(await forbidden.json()).toEqual({ detail: { code: "organization_creation_not_allowed" } });
  });
});
