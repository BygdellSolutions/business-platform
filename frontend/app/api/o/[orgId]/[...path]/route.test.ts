// @vitest-environment node
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as route from "./route";

/**
 * BFF security tests. The BFF is the browser's only door to FastAPI, so these pin down what
 * can and cannot get through. (FastAPI's own refusal of a foreign organization is proven in
 * the Playwright suite against the real backend.)
 */

const ORG = "00000000-0000-4000-8000-0000000000a1";
const OTHER_ORG = "00000000-0000-4000-8000-0000000000b2";
const ORIGIN = "http://localhost:3100";

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("AUTH_MODE", "dev");
  vi.stubEnv("APP_ENV", "development");
  vi.stubEnv("BACKEND_URL", "http://backend.test:8000");
  fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response("[]", { status: 200, headers: { "content-type": "application/json" } })));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

interface Options {
  org?: string;
  path?: string[];
  query?: string;
  headers?: Record<string, string>;
  body?: string;
  cookie?: string | null; // null = no cookie at all
}

function request(method: string, options: Options = {}) {
  const { org = ORG, path = ["customers"], query = "", headers = {}, body, cookie = "maria@dev.test" } = options;
  const url = `${ORIGIN}/api/o/${org}/${path.join("/")}${query}`;
  const init: ConstructorParameters<typeof NextRequest>[1] = { method, headers: { ...(cookie === null ? {} : { cookie: `bp_dev_user=${cookie}` }), ...headers }, body };
  return {
    request: new NextRequest(url, init),
    context: { params: Promise.resolve({ orgId: org, path }) },
  };
}

async function call(method: "GET" | "POST" | "PATCH" | "DELETE", options: Options = {}) {
  const { request: req, context } = request(method, options);
  return route[method](req, context);
}

function sent() {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit & { headers: Headers }];
  return { url, init, headers: init.headers };
}

describe("If-Match (the version a change is based on) is the one client header that is forwarded", () => {
  const PATH = ["transactions", "11111111-1111-4111-8111-111111111111", "complete"];

  it.each([
    ['"3"', '"3"'],
    ["3", '"3"'],
    [' "12" ', '"12"'],
    ['"123456789"', '"123456789"'],
  ])("forwards %j as %j", async (given, forwarded) => {
    await call("POST", { path: PATH, headers: { "if-match": given } });
    expect(sent().headers.get("if-match")).toBe(forwarded);
  });

  it("sends none when the client sent none (the backend then answers 428)", async () => {
    await call("POST", { path: PATH });
    expect(sent().headers.has("if-match")).toBe(false);
  });

  it.each(["abc", "*", '"1" "2"', "-1", "1.5", "", "W/\"1\"", "1234567890", '"1"; DROP'])("refuses %j without calling the backend", async (given) => {
    const response = await call("POST", { path: PATH, headers: { "if-match": given } });
    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ detail: "Invalid If-Match header" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("works for PATCH and DELETE too, and does not let it change identity or organization", async () => {
    await call("PATCH", { path: ["transactions", "x1"], headers: { "if-match": '"2"', "content-type": "application/json", "x-organization-id": OTHER_ORG, "x-dev-user-email": "evil@dev.test" }, body: "{}" });
    const { headers } = sent();
    expect(headers.get("if-match")).toBe('"2"');
    expect(headers.get("x-organization-id")).toBe(ORG);
    expect(headers.get("x-dev-user-email")).toBe("maria@dev.test");
  });

  it("still forwards no other client header", async () => {
    await call("POST", { path: PATH, headers: { "if-match": '"1"', "if-none-match": "*", "x-forwarded-for": "10.0.0.1", authorization: "Bearer x" } });
    const names = [...sent().headers.keys()].sort();
    expect(names).toEqual(["accept", "if-match", "x-dev-user-email", "x-organization-id"]);
  });
});

describe("identity and organization are decided by the BFF, never by the client", () => {
  it("forwards the cookie identity and the URL organization", async () => {
    await call("GET", { query: "?limit=5&q=anna" });

    const { url, init, headers } = sent();
    expect(url).toBe("http://backend.test:8000/api/customers?limit=5&q=anna");
    expect(init.method).toBe("GET");
    expect(headers.get("x-dev-user-email")).toBe("maria@dev.test");
    expect(headers.get("x-organization-id")).toBe(ORG);
  });

  it("ignores identity and organization headers supplied by the client", async () => {
    await call("GET", {
      headers: {
        "x-dev-user-email": "fredrik@dev.test",
        "X-Organization-Id": OTHER_ORG,
        "X-DEV-USER-EMAIL": "evil@dev.test",
      },
    });

    const { headers } = sent();
    expect(headers.get("x-dev-user-email")).toBe("maria@dev.test");
    expect(headers.get("x-organization-id")).toBe(ORG);
  });

  it("sends the backend nothing but the headers it builds itself", async () => {
    await call("GET", {
      headers: {
        authorization: "Bearer stolen",
        "x-forwarded-for": "1.2.3.4",
        "x-forwarded-host": "evil.test",
        host: "evil.test",
        referer: "http://evil.test/",
        "x-real-ip": "1.2.3.4",
        "proxy-authorization": "Basic x",
        "x-custom": "1",
        cookie: "bp_dev_user=maria@dev.test; session=secret; other=1",
      },
    });

    expect([...sent().headers.keys()].sort()).toEqual(["accept", "x-dev-user-email", "x-organization-id"]);
  });

  it("takes the organization from the URL even when a different one is in the query string", async () => {
    await call("GET", { query: `?organization_id=${OTHER_ORG}&x-organization-id=${OTHER_ORG}` });

    expect(sent().headers.get("x-organization-id")).toBe(ORG);
  });

  it("answers 401 without calling the backend when there is no identity cookie, even with forged headers", async () => {
    const response = await call("GET", { cookie: null, headers: { "x-dev-user-email": "fredrik@dev.test", "x-organization-id": ORG } });

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated", login: "/dev-login" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each(["not-an-email", "", "a@b", "a b@c.test"])("answers 401 for a bad cookie value %j", async (cookie) => {
    expect((await call("GET", { cookie })).status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("has no identity at all unless AUTH_MODE selects one: the BFF refuses everything (503), whatever cookie or header comes", async () => {
    vi.stubEnv("AUTH_MODE", "");

    expect((await call("GET")).status).toBe(503);
    expect((await call("GET", { headers: { "x-dev-user-email": "maria@dev.test" } })).status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("only well-formed requests for known API areas get through", () => {
  it.each(["not-a-uuid", "../x", "0".repeat(32), `${ORG}/extra`, "", "%2e%2e"])("404 for the organization id %j", async (org) => {
    const response = await call("GET", { org });
    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [[]],
    [["customers", ".."]],
    [["customers", "%2e%2e", "health"]],
    [["..", "health"]],
    [["customers", "a/b"]],
    [["customers", "a\\b"]],
    [["customers", ""]],
    [["health"]],
    [["docs"]],
    [["openapi.json"]],
    [["admin", "users"]],
    [["customers", "x?y=1"]],
  ])("404 for the path %j, without calling the backend", async (path) => {
    const response = await call("GET", { path });
    expect(response.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("reaches every API area the app needs", async () => {
    for (const area of ["customers", "items", "horses", "transactions", "custom-fields", "me"]) {
      fetchMock.mockClear();
      expect((await call("GET", { path: [area] })).status).toBe(200);
      expect(sent().url).toBe(`http://backend.test:8000/api/${area}`);
    }
  });
});

describe("methods and bodies", () => {
  it("exports exactly GET, POST, PATCH and DELETE", () => {
    expect(Object.keys(route).sort()).toEqual(["DELETE", "GET", "PATCH", "POST"]);
    for (const absent of ["PUT", "HEAD", "OPTIONS", "TRACE", "CONNECT"]) expect(route).not.toHaveProperty(absent);
  });

  it("forwards a JSON body byte for byte, decimal strings included", async () => {
    const body = '{"quantity":"0.250","unit_price_ex_vat":"850.00","vat_rate":"25.00","description":"Hästmassage"}';

    await call("POST", { path: ["transactions", ORG, "lines"], body, headers: { "content-type": "application/json", origin: ORIGIN } });

    const { init, headers } = sent();
    expect(init.method).toBe("POST");
    expect(init.body).toBe(body);
    expect(headers.get("content-type")).toBe("application/json");
  });

  it("forwards PATCH and DELETE", async () => {
    await call("PATCH", { path: ["customers", ORG], body: '{"name":"x"}', headers: { "content-type": "application/json" } });
    expect(sent().init.method).toBe("PATCH");
    fetchMock.mockClear();
    await call("DELETE", { path: ["customers", ORG] });
    expect(sent().init.method).toBe("DELETE");
  });

  it("allows an empty POST (lifecycle actions such as complete)", async () => {
    const response = await call("POST", { path: ["transactions", ORG, "complete"], headers: { origin: ORIGIN } });

    expect(response.status).toBe(200);
    expect(sent().init.body).toBeUndefined();
    expect(sent().headers.has("content-type")).toBe(false);
  });

  it("refuses non-JSON bodies", async () => {
    const response = await call("POST", { body: "name=x", headers: { "content-type": "application/x-www-form-urlencoded" } });

    expect(response.status).toBe(415);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses oversized bodies", async () => {
    const response = await call("POST", { body: JSON.stringify({ x: "y".repeat(1_100_000) }), headers: { "content-type": "application/json" } });

    expect(response.status).toBe(413);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("cross-site requests", () => {
  it.each(["POST", "PATCH", "DELETE"] as const)("refuses a %s from another origin", async (method) => {
    const response = await call(method, { headers: { origin: "http://evil.test", "content-type": "application/json" }, body: method === "DELETE" ? undefined : "{}" });

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses an unparseable origin", async () => {
    expect((await call("POST", { headers: { origin: "null" }, body: "{}" })).status).toBe(403);
  });

  it("accepts the same origin, and requests without an origin header (not a browser form post)", async () => {
    expect((await call("POST", { headers: { origin: ORIGIN, "content-type": "application/json" }, body: "{}" })).status).toBe(200);
    expect((await call("POST", { headers: { "content-type": "application/json" }, body: "{}" })).status).toBe(200);
  });
});

describe("responses", () => {
  it.each([
    [403, { detail: "Your role in this organization does not allow this action" }],
    [404, { detail: "Organization not found" }],
    [409, { detail: { code: "validation_failed", problems: [] } }],
    [422, { detail: [{ loc: ["body", "name"], msg: "Field required", type: "missing" }] }],
  ])("passes %i through with its body untouched", async (status, body) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));

    const response = await call("GET");

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual(body);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  it("answers a backend 401 with a fixed body naming the login page, never the backend's own body", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "Not authenticated", secret: "token=abc" }), { status: 401, headers: { "content-type": "application/json" } }));

    const response = await call("GET");

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ detail: "Not authenticated", login: "/dev-login" });
  });

  it("returns only the content type: no cookies or other backend headers leak to the browser", async () => {
    fetchMock.mockResolvedValue(
      new Response("{}", { status: 200, headers: { "content-type": "application/json", "set-cookie": "session=abc", "x-secret": "1", server: "uvicorn", "access-control-allow-origin": "*" } }),
    );

    const response = await call("GET");

    expect([...response.headers.keys()].sort()).toEqual(["cache-control", "content-type"]);
  });

  it("passes 204 through without a body", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    const response = await call("DELETE", { path: ["customers", ORG] });

    expect(response.status).toBe(204);
    expect(await response.text()).toBe("");
  });

  it("turns a backend redirect into an error instead of following or forwarding it", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 307, headers: { location: "http://evil.test/" } }));

    const response = await call("GET");

    expect(response.status).toBe(502);
    expect(response.headers.get("location")).toBeNull();
  });

  it("answers 502 when the backend cannot be reached or times out", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));
    expect((await call("GET")).status).toBe(502);

    fetchMock.mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    const response = await call("GET");
    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({ detail: "Backend unavailable" });
  });
});
