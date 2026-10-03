import { describe, expect, it } from "vitest";

import { PAGE_SIZE, backendQuery, listHref, pageOf, parseListParams } from "@/lib/list-params";

describe("parseListParams: the address is untrusted input", () => {
  it("defaults to the first page of everything", () => {
    expect(parseListParams({})).toEqual({ q: "", active: "all", type: "", refs: {}, page: 1 });
  });

  it("reads known values", () => {
    expect(parseListParams({ q: "  anna ", active: "inactive", type: "service", page: "3" }, ["service", "product"])).toEqual({
      q: "anna",
      active: "inactive",
      type: "service",
      refs: {},
      page: 3,
    });
  });

  it("reads a record filter only if the list offers it and the value is a UUID", () => {
    const owner = "00000000-0000-4000-8000-0000000000A1";
    const keys = ["owner_customer_id", "stable_customer_id"];

    expect(parseListParams({ owner_customer_id: owner, stable_customer_id: "not-a-uuid", organization_id: owner }, [], keys).refs).toEqual({
      owner_customer_id: owner.toLowerCase(),
    });
    expect(parseListParams({ owner_customer_id: owner }).refs).toEqual({}); // not offered by this list
    expect(parseListParams({ owner_customer_id: `${owner}' or 1=1` }, [], keys).refs).toEqual({});
  });

  it.each([["0"], ["-1"], ["1.5"], ["abc"], ["1e3"], ["100000"], [""], ["01"]])("drops the malformed page %j", (page) => {
    expect(parseListParams({ page }).page).toBe(1);
  });

  it("drops filter values the list does not offer instead of forwarding them", () => {
    const params = parseListParams({ active: "true", type: "horse; drop table" }, ["service", "product"]);
    expect(params.active).toBe("all");
    expect(params.type).toBe("");
  });

  it("an item type is ignored where the list has no types", () => {
    expect(parseListParams({ type: "service" }).type).toBe("");
  });

  it("uses the first of repeated parameters and caps the search length", () => {
    expect(parseListParams({ q: ["first", "second"] }).q).toBe("first");
    expect(parseListParams({ q: "x".repeat(1000) }).q).toHaveLength(255);
  });
});

describe("backendQuery", () => {
  it("asks for one row more than a page, so the page knows whether there is a next one", () => {
    expect(backendQuery(parseListParams({}))).toBe(`?limit=${PAGE_SIZE + 1}&offset=0`);
  });

  it("translates the filters to the backend parameters and computes the offset", () => {
    const query = new URLSearchParams(backendQuery(parseListParams({ q: "a&b=c", active: "active", type: "product", page: "3" }, ["product"])));
    expect(query.get("q")).toBe("a&b=c"); // encoded, so it cannot add parameters
    expect(query.get("active")).toBe("true");
    expect(query.get("type")).toBe("product");
    expect(query.get("offset")).toBe(String(2 * PAGE_SIZE));
    expect([...query.keys()].sort()).toEqual(["active", "limit", "offset", "q", "type"]);
  });

  it("forwards a record filter under its backend name", () => {
    const owner = "00000000-0000-4000-8000-0000000000a1";
    const params = parseListParams({ owner_customer_id: owner }, [], ["owner_customer_id"]);
    expect(new URLSearchParams(backendQuery(params)).get("owner_customer_id")).toBe(owner);
  });

  it("maps inactive to active=false", () => {
    expect(new URLSearchParams(backendQuery(parseListParams({ active: "inactive" }))).get("active")).toBe("false");
  });
});

describe("listHref", () => {
  const params = parseListParams({ q: "anna", active: "active", page: "2" });

  it("keeps the organization-scoped base and leaves defaults out", () => {
    expect(listHref("/o/x/customers", parseListParams({}))).toBe("/o/x/customers");
    expect(listHref("/o/x/customers", params)).toBe("/o/x/customers?q=anna&active=active&page=2");
  });

  it("keeps record filters in the address", () => {
    const owner = "00000000-0000-4000-8000-0000000000a1";
    const withOwner = parseListParams({ owner_customer_id: owner, page: "2" }, [], ["owner_customer_id"]);
    expect(listHref("/o/x/horses", withOwner)).toBe(`/o/x/horses?owner_customer_id=${owner}&page=2`);
    expect(listHref("/o/x/horses", withOwner, { page: 1 })).toBe(`/o/x/horses?owner_customer_id=${owner}`);
  });

  it("changes only what is asked", () => {
    expect(listHref("/o/x/customers", params, { page: 3 })).toBe("/o/x/customers?q=anna&active=active&page=3");
    expect(listHref("/o/x/customers", params, { page: 1 })).toBe("/o/x/customers?q=anna&active=active");
  });
});

describe("pageOf", () => {
  it("shows a page and reports whether more rows exist", () => {
    const rows = Array.from({ length: PAGE_SIZE + 1 }, (_, index) => index);
    expect(pageOf(rows)).toEqual({ rows: rows.slice(0, PAGE_SIZE), hasNext: true });
    expect(pageOf(rows.slice(0, PAGE_SIZE))).toMatchObject({ hasNext: false });
    expect(pageOf([])).toEqual({ rows: [], hasNext: false });
  });
});
