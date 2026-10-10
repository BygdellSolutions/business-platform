import { describe, expect, it } from "vitest";

import { NAV } from "@/components/shell/nav";

describe("navigation", () => {
  it("links the organization settings", () => {
    expect(NAV.find((item) => item.label === "Settings")).toEqual({ label: "Settings", path: "/settings", enabled: true });
  });

  it("links the inventory overview (backorders and incoming stock)", () => {
    expect(NAV.find((item) => item.label === "Inventory")).toEqual({ label: "Inventory", path: "/inventory", enabled: true });
  });

  it("links the invoices", () => {
    expect(NAV.find((item) => item.label === "Invoices")).toEqual({ label: "Invoices", path: "/invoices", enabled: true });
  });

  it("shows Members to owners and admins only (presentation; the backend decides)", () => {
    expect(NAV.find((item) => item.label === "Members")).toEqual({ label: "Members", path: "/members", enabled: true, roles: ["owner", "admin"] });
  });

  it("has unique labels and paths, all relative to the organization", () => {
    expect(new Set(NAV.map((item) => item.label)).size).toBe(NAV.length);
    expect(new Set(NAV.map((item) => item.path)).size).toBe(NAV.length);
    for (const item of NAV) expect(item.path === "" || item.path.startsWith("/")).toBe(true);
  });
});
