// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ALLOWED_API_AREAS, apiPathFromSegments, backendFetch, buildBackendHeaders, isUuid, parseIfMatch } from "@/lib/backend";

const ORG = "00000000-0000-4000-8000-0000000000a1";

describe("isUuid", () => {
  it.each([ORG, ORG.toUpperCase(), "123e4567-e89b-12d3-a456-426614174000"])("accepts %s", (value) => {
    expect(isUuid(value)).toBe(true);
  });

  it.each(["", "not-a-uuid", `${ORG}x`, ` ${ORG}`, `${ORG}/`, "../" + ORG, "0".repeat(32), undefined, null])("rejects %j", (value) => {
    expect(isUuid(value as string)).toBe(false);
  });
});

describe("apiPathFromSegments", () => {
  it.each([
    [["customers"], "/api/customers"],
    [["customers", ORG], `/api/customers/${ORG}`],
    [["transactions", ORG, "lines", ORG], `/api/transactions/${ORG}/lines/${ORG}`],
    [["transactions", ORG, "complete"], `/api/transactions/${ORG}/complete`],
    [["custom-fields", "entity-types"], "/api/custom-fields/entity-types"],
    [["custom-fields", "entities", "transaction_line", ORG, "values"], `/api/custom-fields/entities/transaction_line/${ORG}/values`],
    [["me", "organizations"], "/api/me/organizations"],
  ])("maps %j to %s", (segments, expected) => {
    expect(apiPathFromSegments(segments)).toBe(expected);
  });

  it.each([
    [undefined],
    [[]],
    [["customers", ".."]],
    [["customers", "."]],
    [["..", "customers"]],
    [["customers", "a/b"]],
    [["customers", "a\\b"]],
    [["customers", ""]],
    [["customers", "%2e%2e"]],
    [["customers", "%2F"]],
    [["customers", "a b"]],
    [["customers", "x?y=1"]],
    [["customers", "x#y"]],
    [["customers", "x;y"]],
    [["customers", "ünï"]],
    [["customers", "a\u0000b"]],
    [["customers", "-leading-dash"]],
    [["health"]],
    [["docs"]],
    [["openapi.json"]],
    [["admin"]],
    [["api", "customers"]],
    [["Customers"]],
    [["customers", "1", "2", "3", "4", "5", "6"]],
  ])("rejects %j", (segments) => {
    expect(apiPathFromSegments(segments as string[] | undefined)).toBeNull();
  });

  it("only allows the documented API areas", () => {
    expect([...ALLOWED_API_AREAS].sort()).toEqual(["custom-fields", "customers", "horses", "inventory", "invitations", "invoiceable-transactions", "invoices", "items", "me", "members", "organization", "suppliers", "transactions"]);
  });
});

describe("parseIfMatch", () => {
  it.each([
    [null, null],
    [undefined, null],
    ['"3"', '"3"'],
    ["3", '"3"'],
    [" 7 ", '"7"'],
    ["000012", '"000012"'],
  ])("%j gives %j", (value, expected) => {
    expect(parseIfMatch(value)).toBe(expected);
  });

  it.each(["", "abc", "*", "-1", "1.5", '"1" "2"', "W/\"1\"", "1234567890", '"1"x', "0x10", "1e3"])("%j is invalid", (value) => {
    expect(parseIfMatch(value)).toBe("invalid");
  });
});

describe("buildBackendHeaders: built from scratch, only what the BFF controls", () => {
  it("adds If-Match only when given", () => {
    expect(buildBackendHeaders({ credential: { kind: "dev", email: "a@b.test" } }, { ifMatch: '"4"' }).get("if-match")).toBe('"4"');
    expect(buildBackendHeaders({ credential: { kind: "dev", email: "a@b.test" } }).has("if-match")).toBe(false);
  });

  it("sets identity and organization", () => {
    const headers = buildBackendHeaders({ credential: { kind: "dev", email: "maria@dev.test" }, orgId: ORG });

    expect(Object.fromEntries(headers)).toEqual({
      accept: "application/json",
      "x-dev-user-email": "maria@dev.test",
      "x-organization-id": ORG,
    });
  });

  it("omits the organization when there is none, and adds a JSON content type only for bodies", () => {
    expect(Object.fromEntries(buildBackendHeaders({ credential: { kind: "dev", email: "a@b.test" } }))).toEqual({ accept: "application/json", "x-dev-user-email": "a@b.test" });
    expect(buildBackendHeaders({ credential: { kind: "dev", email: "a@b.test" } }, { json: true }).get("content-type")).toBe("application/json");
  });
});

describe("backendFetch", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response("[]"));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubEnv("BACKEND_URL", "http://backend.test:9000/");
    vi.stubEnv("APP_ENV", "development"); // production accepts only a private backend address
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("calls the configured backend, uncached, without following redirects", async () => {
    await backendFetch({ credential: { kind: "dev", email: "a@b.test" }, orgId: ORG }, "/api/customers", { search: "?limit=5" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://backend.test:9000/api/customers?limit=5");
    expect(init).toMatchObject({ method: "GET", cache: "no-store", redirect: "manual" });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it.each(["/health", "/docs", "api/customers", "/api/../health", "/api//customers", "http://evil.test/api/x"])("refuses %s", async (path) => {
    await expect(backendFetch({ credential: { kind: "dev", email: "a@b.test" } }, path)).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses a malformed organization id", async () => {
    await expect(backendFetch({ credential: { kind: "dev", email: "a@b.test" }, orgId: "../x" }, "/api/customers")).rejects.toThrow();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
