// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as login from "@/app/api/auth/login/route";
import * as catchAll from "@/app/api/o/[orgId]/[...path]/route";
import * as organizations from "@/app/api/organizations/route";
import * as preview from "@/app/api/invite/preview/route";
import { backendFetch, buildBackendHeaders } from "@/lib/backend";

/**
 * What the BFF tells FastAPI, and what it never lets the browser tell it. Every upstream request carries the BFF's OWN
 * internal secret and request id; every value the browser could try to inject (the secret, the id, a client address,
 * forwarding, identity, organization, role or authorization headers) is ignored or replaced.
 */

const ORIGIN = "http://127.0.0.1:3100";
const ORG = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ORG = "00000000-0000-4000-8000-0000000000b2";
const SECRET = "9f3c1a7e5b2d8046c1e7a95b3d20f648a1c7e903d5b6f2a8";
const BROWSER_SECRET = "BROWSER-CHOSEN-" + "z".repeat(40);
const SESSION = "S".repeat(43);
const CSRF = "C".repeat(43);
const PRE = "P".repeat(43);
const TOKEN = "T".repeat(43);

let fetchMock: ReturnType<typeof vi.fn>;
let logged: string[];

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "session");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("PUBLIC_ORIGIN", ORIGIN);
  vi.stubEnv("BACKEND_URL", "http://backend:8000");
  vi.stubEnv("BFF_INTERNAL_SECRET", SECRET);
  vi.stubEnv("TRUSTED_PROXY_HOPS", "0");
  fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } })));
  vi.stubGlobal("fetch", fetchMock);
  logged = [];
  vi.spyOn(process.stdout, "write").mockImplementation(((chunk: string) => (logged.push(String(chunk)), true)) as never);
  vi.spyOn(process.stderr, "write").mockImplementation(((chunk: string) => (logged.push(String(chunk)), true)) as never);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const upstreamHeaders = (index = 0): Headers => (fetchMock.mock.calls[index][1] as { headers: Headers }).headers;

const FORGED = {
  "x-bff-secret": BROWSER_SECRET,
  "x-internal-secret": BROWSER_SECRET,
  "x-request-id": "browser-chosen-request-id-123456",
  "x-client-ip": "6.6.6.6",
  "x-forwarded-for": "6.6.6.6",
  "x-forwarded-host": "evil.example",
  "x-forwarded-proto": "https",
  "x-real-ip": "6.6.6.6",
  forwarded: "for=6.6.6.6",
  authorization: "Bearer stolen",
  "proxy-authorization": "Basic x",
  "x-dev-user-email": "maria@dev.test",
  "x-organization-id": OTHER_ORG,
  "x-user-id": "1",
  "x-user-role": "owner",
  "x-role": "owner",
  "x-owner-email": "x@y.test",
  "x-actor": "root",
};

function scoped(method: "GET" | "POST", headers: Record<string, string> = {}, cookies = `bp_session=${SESSION}; bp_csrf=${CSRF}`) {
  const request = new NextRequest(`${ORIGIN}/api/o/${ORG}/customers`, {
    method,
    headers: { cookie: cookies, ...(method === "POST" ? { origin: ORIGIN, "x-csrf-token": CSRF, "content-type": "application/json" } : {}), ...headers },
    body: method === "POST" ? "{}" : undefined,
  });
  return catchAll[method](request, { params: Promise.resolve({ orgId: ORG, path: ["customers"] }) });
}

describe("the BFF's own trust headers", () => {
  it.each(["GET", "POST"] as const)("%s through the organization door carries the configured secret and a BFF-generated request id", async (method) => {
    await scoped(method);
    expect(upstreamHeaders().get("x-bff-secret")).toBe(SECRET);
    expect(upstreamHeaders().get("x-request-id")).toMatch(/^[0-9a-f]{32}$/);
  });

  it("organization creation, login and invitation preview carry them too (every upstream request does)", async () => {
    await organizations.POST(new NextRequest(`${ORIGIN}/api/organizations`, { method: "POST", headers: { cookie: `bp_session=${SESSION}; bp_csrf=${CSRF}`, origin: ORIGIN, "x-csrf-token": CSRF, "content-type": "application/json" }, body: "{}" }));
    fetchMock.mockImplementationOnce(() => Promise.resolve(new Response("{}", { status: 401 })));
    await login.POST(new NextRequest(`${ORIGIN}/api/auth/login`, { method: "POST", headers: { origin: ORIGIN, "x-pre-auth": PRE, cookie: `bp_pre=${PRE}`, "content-type": "application/json" }, body: JSON.stringify({ email: "a@b.test", password: "x" }) }));
    await preview.POST(new NextRequest(`${ORIGIN}/api/invite/preview`, { method: "POST", headers: { origin: ORIGIN, "x-pre-auth": PRE, cookie: `bp_pre=${PRE}`, "content-type": "application/json" }, body: JSON.stringify({ token: TOKEN }) }));
    expect(fetchMock).toHaveBeenCalledTimes(3);
    for (let index = 0; index < 3; index += 1) {
      expect(upstreamHeaders(index).get("x-bff-secret"), `call ${index}`).toBe(SECRET);
      expect(upstreamHeaders(index).get("x-request-id"), `call ${index}`).toMatch(/^[0-9a-f]{32}$/);
    }
  });

  it("two requests get two different ids", async () => {
    await scoped("GET");
    await scoped("GET");
    expect(upstreamHeaders(0).get("x-request-id")).not.toBe(upstreamHeaders(1).get("x-request-id"));
  });

  it("the browser's copy of the id is returned as ours, never forwarded", async () => {
    const response = await scoped("GET", { "x-request-id": FORGED["x-request-id"] });
    expect(upstreamHeaders().get("x-request-id")).not.toBe(FORGED["x-request-id"]);
    expect(response.headers.get("x-request-id")).toBe(upstreamHeaders().get("x-request-id")); // the same id the backend logs
  });

  it("sends no secret when none is configured (the documented development mode); production requires one at startup", async () => {
    vi.stubEnv("BFF_INTERNAL_SECRET", "");
    await scoped("GET");
    expect(upstreamHeaders().has("x-bff-secret")).toBe(false);
  });
});

describe("nothing the browser sends can become a trust header", () => {
  it.each(["GET", "POST"] as const)("%s: every forged identity, tenant, role, forwarding, secret and id header is dropped or replaced", async (method) => {
    await scoped(method, FORGED);
    const headers = upstreamHeaders();
    expect(headers.get("x-bff-secret")).toBe(SECRET); // ours, not the browser's
    expect(headers.get("x-organization-id")).toBe(ORG); // from the URL
    expect(headers.get("authorization")).toBe(`Bearer ${SESSION}`); // from the protected cookie
    expect(headers.get("x-request-id")).not.toBe(FORGED["x-request-id"]);
    for (const name of ["x-internal-secret", "x-client-ip", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded", "proxy-authorization", "x-dev-user-email", "x-user-id", "x-user-role", "x-role", "x-owner-email", "x-actor", "cookie"]) {
      expect(headers.has(name), name).toBe(false);
    }
    const allowed = new Set(["accept", "authorization", "x-organization-id", "x-bff-secret", "x-request-id", "content-type", "x-csrf-token"]);
    expect([...headers.keys()].filter((name) => !allowed.has(name))).toEqual([]);
    expect([...headers.values()].join("\n")).not.toContain(BROWSER_SECRET);
  });

  it("an invited-but-unauthenticated door (preview) also forwards nothing of it", async () => {
    await preview.POST(new NextRequest(`${ORIGIN}/api/invite/preview`, { method: "POST", headers: { origin: ORIGIN, "x-pre-auth": PRE, cookie: `bp_pre=${PRE}; bp_session=${SESSION}`, "content-type": "application/json", ...FORGED }, body: JSON.stringify({ token: TOKEN }) }));
    const headers = upstreamHeaders();
    expect([...headers.keys()].sort()).toEqual(["accept", "content-type", "x-bff-secret", "x-request-id"]);
    expect(headers.get("x-bff-secret")).toBe(SECRET);
  });

  it("buildBackendHeaders builds from scratch: even an unrelated Headers object has no way in", () => {
    const headers = buildBackendHeaders({ credential: { kind: "session", token: SESSION }, orgId: ORG }, { requestId: "r".repeat(32) });
    expect([...headers.keys()].sort()).toEqual(["accept", "authorization", "x-bff-secret", "x-organization-id", "x-request-id"]);
  });
});

describe("the client address: only what the BFF derived from the verified proxy chain", () => {
  const loginWith = (headers: Record<string, string>) =>
    login.POST(new NextRequest(`${ORIGIN}/api/auth/login`, { method: "POST", headers: { origin: ORIGIN, "x-pre-auth": PRE, cookie: `bp_pre=${PRE}`, "content-type": "application/json", ...headers }, body: JSON.stringify({ email: "a@b.test", password: "x" }) }));

  it("with no trusted proxy (the default until the topology is verified) NO client address is forwarded, forged or not", async () => {
    await loginWith({ "x-forwarded-for": "6.6.6.6", "x-client-ip": "9.9.9.9", "x-real-ip": "8.8.8.8" });
    expect(upstreamHeaders().has("x-client-ip")).toBe(false);
  });

  it.each([
    ["one hop: the entry the trusted proxy appended wins over what the browser wrote", "1", "6.6.6.6, 198.51.100.7", "198.51.100.7"],
    ["two hops", "2", "6.6.6.6, 198.51.100.7, 10.0.0.9", "198.51.100.7"],
    ["IPv6", "1", "1.1.1.1, 2001:db8::7", "2001:db8::7"],
    ["a single entry with one hop", "1", "198.51.100.7", "198.51.100.7"],
  ])("%s", async (_name, hops, forwarded, expected) => {
    vi.stubEnv("TRUSTED_PROXY_HOPS", hops);
    await loginWith({ "x-forwarded-for": forwarded, "x-client-ip": "9.9.9.9" });
    expect(upstreamHeaders().get("x-client-ip")).toBe(expected);
  });

  it.each([
    ["too few entries for the hops", "2", "198.51.100.7"],
    ["no forwarding header at all", "1", ""],
    ["a malformed entry", "1", "6.6.6.6, not-an-ip"],
    ["an entry with a port", "1", "198.51.100.7:4444"],
    ["a script", "1", "6.6.6.6, <script>"],
    ["empty entries", "1", "198.51.100.7,"],
  ])("forwards nothing for %s (it is not guessed)", async (_name, hops, forwarded) => {
    vi.stubEnv("TRUSTED_PROXY_HOPS", hops);
    await loginWith({ ...(forwarded ? { "x-forwarded-for": forwarded } : {}), "x-client-ip": "9.9.9.9" });
    expect(upstreamHeaders().has("x-client-ip")).toBe(false);
  });

  it("separate X-Forwarded-For header lines are one list, so appending proxies still work", async () => {
    vi.stubEnv("TRUSTED_PROXY_HOPS", "2");
    const headers = new Headers({ origin: ORIGIN, "x-pre-auth": PRE, cookie: `bp_pre=${PRE}`, "content-type": "application/json" });
    headers.append("x-forwarded-for", "6.6.6.6");
    headers.append("x-forwarded-for", "198.51.100.7");
    headers.append("x-forwarded-for", "10.0.0.9");
    await login.POST(new NextRequest(`${ORIGIN}/api/auth/login`, { method: "POST", headers, body: JSON.stringify({ email: "a@b.test", password: "x" }) }));
    expect(upstreamHeaders().get("x-client-ip")).toBe("198.51.100.7");
  });
});

describe("the secret never leaves the server", () => {
  it("is in no response body, response header or log line, even when the backend fails", async () => {
    fetchMock.mockRejectedValue(Object.assign(new TypeError("fetch failed"), { cause: new Error(`ECONNREFUSED backend:8000 ${SECRET}`) }));
    const response = await scoped("GET");
    fetchMock.mockImplementation(() => Promise.resolve(new Response("boom", { status: 500, headers: { "x-bff-secret": SECRET } })));
    const second = await scoped("GET");
    for (const r of [response, second]) {
      expect(await r.text()).not.toContain(SECRET);
      expect([...r.headers.values()].join("\n")).not.toContain(SECRET);
    }
    expect(logged.join("")).not.toContain(SECRET);
    expect(logged.join("")).not.toContain("backend:8000");
  });

  it("is a server-only value: nothing public carries it", () => {
    expect(Object.keys(process.env).filter((name) => name.startsWith("NEXT_PUBLIC_"))).toEqual([]);
  });

  it("backendFetch itself sends it (server components and the shell use it too)", async () => {
    await backendFetch({ credential: { kind: "session", token: SESSION } }, "/api/me/organizations");
    expect(upstreamHeaders().get("x-bff-secret")).toBe(SECRET);
  });
});
