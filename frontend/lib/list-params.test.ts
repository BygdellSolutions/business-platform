import { describe, expect, it } from "vitest";

import { PAGE_SIZE, backendQuery, listHref, pageOf, parseListParams, sortHref } from "@/lib/list-params";

describe("parseListParams: the address is untrusted input", () => {
  it("defaults to the first page of everything", () => {
    expect(parseListParams({})).toEqual({ q: "", active: "all", type: "", refs: {}, extra: {}, page: 1, sort: "", dir: "asc" });
  });

  it("reads known values", () => {
    expect(parseListParams({ q: "  anna ", active: "inactive", type: "service", page: "3" }, ["service", "product"])).toEqual({
      q: "anna",
      active: "inactive",
      type: "service",
      refs: {},
      extra: {},
      page: 3,
      sort: "",
      dir: "asc",
    });
  });

  it("accepts only the sort keys the list offers, and keeps the order in its links and backend query", () => {
    const sorted = parseListParams({ sort: "price", dir: "desc", page: "2" }, [], [], {}, ["price", "name"]);
    expect([sorted.sort, sorted.dir]).toEqual(["price", "desc"]);
    expect(backendQuery(sorted)).toContain("sort=price&dir=desc");
    expect(listHref("/l", sorted)).toBe("/l?sort=price&dir=desc&page=2");
    expect(sortHref("/l", sorted, "price")).toBe("/l?sort=price"); // turned around, back to page 1
    expect(sortHref("/l", sorted, "name")).toBe("/l?sort=name");
    expect(sortHref("/l", { ...sorted, dir: "asc" }, "price")).toBe("/l?sort=price&dir=desc");
    expect(parseListParams({ sort: "organization_id", dir: "desc" }, [], [], {}, ["price"])).toMatchObject({ sort: "", dir: "asc" });
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

  it("reads a choice filter and date filters under their backend names, and drops anything else", () => {
    const specs = { status: ["draft", "completed", "cancelled"], date_from: "date", date_to: "date" } as const;
    const params = parseListParams({ status: "completed", date_from: "2026-10-01", date_to: "tomorrow", other: "x" }, [], [], specs);
    expect(params.extra).toEqual({ status: "completed", date_from: "2026-10-01" });

    expect(parseListParams({ status: "deleted", date_from: "2026-1-1" }, [], [], specs).extra).toEqual({});
    expect(parseListParams({ status: "completed" }).extra).toEqual({}); // not offered by this list
    expect(parseListParams({ status: ["draft", "completed"] }, [], [], specs).extra).toEqual({ status: "draft" });
  });

  it("forwards extra filters to the backend and keeps them in the address", () => {
    const specs = { status: ["draft", "completed"], date_to: "date" } as const;
    const params = parseListParams({ status: "draft", date_to: "2026-12-31", page: "2" }, [], [], specs);

    const query = new URLSearchParams(backendQuery(params));
    expect(query.get("status")).toBe("draft");
    expect(query.get("date_to")).toBe("2026-12-31");
    expect(listHref("/o/x/transactions", params)).toBe("/o/x/transactions?status=draft&date_to=2026-12-31&page=2");
    expect(listHref("/o/x/transactions", params, { page: 1 })).toBe("/o/x/transactions?status=draft&date_to=2026-12-31");
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
