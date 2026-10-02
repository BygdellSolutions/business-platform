import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiFetch, bffPath, setUnauthorizedHandler } from "@/lib/api/client";
import { parseMoney, parseQuantity } from "@/lib/decimal";

const ORG = "00000000-0000-4000-8000-0000000000a1";

function respond(status: number, body?: unknown) {
  const text = body === undefined ? "" : JSON.stringify(body);
  return Promise.resolve(new Response(status === 204 ? null : text, { status, headers: { "content-type": "application/json" } }));
}

let fetchMock: ReturnType<typeof vi.fn>;
let unauthorized: ReturnType<typeof vi.fn<() => void>>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  unauthorized = vi.fn<() => void>();
  setUnauthorizedHandler(unauthorized);
});
afterEach(() => vi.unstubAllGlobals());

describe("bffPath", () => {
  it("scopes every path to the organization", () => {
    expect(bffPath(ORG, "/customers?limit=5")).toBe(`/api/o/${ORG}/customers?limit=5`);
  });

  it("encodes the organization and requires a leading slash", () => {
    expect(bffPath("a/b", "/x")).toBe("/api/o/a%2Fb/x");
    expect(() => bffPath(ORG, "customers")).toThrow();
  });
});

describe("apiFetch", () => {
  it("calls the same-origin BFF and sends NO identity or organization headers", async () => {
    fetchMock.mockReturnValue(respond(200, []));

    await apiFetch(ORG, "/customers");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`/api/o/${ORG}/customers`);
    const names = Object.keys(init.headers).map((n) => n.toLowerCase());
    expect(names).toEqual(["accept"]);
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
  });

  it("returns data on success", async () => {
    fetchMock.mockReturnValue(respond(200, [{ id: "1" }]));
    expect(await apiFetch(ORG, "/customers")).toEqual({ ok: true, status: 200, data: [{ id: "1" }] });
  });

  it("sends JSON bodies and keeps decimal strings as strings", async () => {
    fetchMock.mockReturnValue(respond(201, { id: "x" }));
    const price = parseMoney("850.00");
    const quantity = parseQuantity("0.250");

    await apiFetch(ORG, "/transactions/T/lines", { method: "POST", body: { quantity, unit_price_ex_vat: price, vat_rate: "25.00" } });

    const init = fetchMock.mock.calls[0][1];
    expect(init.method).toBe("POST");
    expect(init.headers["content-type"]).toBe("application/json");
    expect(init.body).toBe('{"quantity":"0.250","unit_price_ex_vat":"850.00","vat_rate":"25.00"}');
    expect(JSON.parse(init.body).unit_price_ex_vat).toBeTypeOf("string");
  });

  it("handles 204 without a body", async () => {
    fetchMock.mockReturnValue(respond(204));
    expect(await apiFetch(ORG, "/customers/1", { method: "DELETE" })).toMatchObject({ ok: true, status: 204 });
  });

  it.each([
    [403, "forbidden"],
    [404, "not_found"],
    [409, "conflict"],
    [422, "validation"],
    [500, "server"],
  ] as const)("maps %i to %s", async (status, kind) => {
    fetchMock.mockReturnValue(respond(status, { detail: "x" }));
    const result = await apiFetch(ORG, "/customers");
    expect(result.ok).toBe(false);
    expect(!result.ok && result.error.kind).toBe(kind);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("calls the unauthorized handler on 401", async () => {
    fetchMock.mockReturnValue(respond(401, { detail: "Not authenticated" }));

    const result = await apiFetch(ORG, "/customers");

    expect(!result.ok && result.error.kind).toBe("unauthorized");
    expect(unauthorized).toHaveBeenCalledTimes(1);
  });

  it("reports a network failure as a result, not an exception", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    expect(await apiFetch(ORG, "/customers")).toMatchObject({ ok: false, error: { kind: "network" } });
  });

  it("rethrows a cancelled request instead of showing an error", async () => {
    const controller = new AbortController();
    controller.abort();
    fetchMock.mockRejectedValue(new DOMException("aborted", "AbortError"));
    await expect(apiFetch(ORG, "/customers", { signal: controller.signal })).rejects.toThrow();
  });

  it("copes with a non-JSON error body", async () => {
    fetchMock.mockReturnValue(Promise.resolve(new Response("<html>bad gateway</html>", { status: 502 })));
    expect(await apiFetch(ORG, "/customers")).toMatchObject({ ok: false, error: { kind: "server", status: 502 } });
  });
});
