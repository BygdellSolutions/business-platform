import { describe, expect, it } from "vitest";

import { NAV } from "@/components/shell/nav";

describe("navigation", () => {
  it("links the organization settings", () => {
    expect(NAV.find((item) => item.label === "Settings")).toEqual({ label: "Settings", path: "/settings", enabled: true });
  });

  it("has unique labels and paths, all relative to the organization", () => {
    expect(new Set(NAV.map((item) => item.label)).size).toBe(NAV.length);
    expect(new Set(NAV.map((item) => item.path)).size).toBe(NAV.length);
    for (const item of NAV) expect(item.path === "" || item.path.startsWith("/")).toBe(true);
  });
});
