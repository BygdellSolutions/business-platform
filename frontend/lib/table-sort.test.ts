import { describe, expect, it } from "vitest";

import { compareDecimal } from "@/lib/decimal";
import { sortRows, tableSort } from "@/lib/table-sort";

describe("compareDecimal", () => {
  it("orders decimal strings exactly, without converting them to numbers", () => {
    const values = ["10.00", "9.5", "-2", "0.10", "0.1", "100", "-10.5", "009.50", "12345678901234567890.01", "12345678901234567890.001"];
    expect([...values].sort(compareDecimal)).toEqual(["-10.5", "-2", "0.10", "0.1", "9.5", "009.50", "10.00", "100", "12345678901234567890.001", "12345678901234567890.01"]);
    expect(compareDecimal("1.50", "1.5")).toBe(0);
  });
});

describe("tableSort", () => {
  it("reads only the keys the table offers and keeps the other parameters in its links", () => {
    const sort = tableSort({ q: "anna", deliveries: "cost", deliveries_dir: "desc", other: ["a", "b"] }, "/o/x/suppliers/1", ["cost", "date"], "deliveries");
    expect([sort.sort, sort.dir]).toEqual(["cost", "desc"]);
    expect(sort.hrefs.cost).toBe("/o/x/suppliers/1?q=anna&deliveries=cost");
    expect(sort.hrefs.date).toBe("/o/x/suppliers/1?q=anna&deliveries=date");
    expect(tableSort({ deliveries: "organization_id", deliveries_dir: "desc" }, "/b", ["cost"], "deliveries")).toMatchObject({ sort: "", dir: "asc" });
  });

  it("turns the order around on a second click of the same column", () => {
    expect(tableSort({ sort: "date" }, "/b", ["date"]).hrefs.date).toBe("/b?sort=date&sort_dir=desc");
  });
});

describe("sortRows", () => {
  const rows = [
    { name: "bo", cost: "10.00", qty: 3 },
    { name: "Anna", cost: null, qty: 1 },
    { name: "Cia", cost: "9.50", qty: 2 },
  ];
  const columns = { name: (r: (typeof rows)[number]) => ({ text: r.name }), cost: (r: (typeof rows)[number]) => ({ decimal: r.cost }), qty: (r: (typeof rows)[number]) => ({ number: r.qty }) };
  const by = (sort: string, dir: "asc" | "desc") => sortRows(rows, { sort, dir }, columns).map((r) => r.name);

  it("sorts text without case, decimals exactly and numbers, both ways, empty values last", () => {
    expect(by("name", "asc")).toEqual(["Anna", "bo", "Cia"]);
    expect(by("cost", "asc")).toEqual(["Cia", "bo", "Anna"]);
    expect(by("cost", "desc")).toEqual(["bo", "Cia", "Anna"]);
    expect(by("qty", "desc")).toEqual(["bo", "Cia", "Anna"]);
    expect(by("", "asc")).toEqual(["bo", "Anna", "Cia"]); // unsorted: as received
  });
});
