import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The owner asked (2026-10-10) for an order's number and its date in SEPARATE columns everywhere: "Order 1001 ·
 * 2026-10-10" in one table cell is refused. The order page's own heading is the one place that names an order that way.
 */
const ROOTS = ["app", "features", "components"];
const HEADING = join("app", "o", "[orgId]", "transactions", "[id]", "page.tsx");
const COMBINED = /\{[\w.]*number\}\s*·\s*\{[\w.]*transaction_date\}/;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

describe("order numbers and dates", () => {
  it("are never combined in one cell", () => {
    const files = ROOTS.flatMap(sources);
    expect(files).toContain(HEADING); // the scan sees the app (a control)
    expect(COMBINED.test(readFileSync(HEADING, "utf8"))).toBe(true); // ...and the pattern finds the one allowed use
    const offenders = files.filter((file) => file !== HEADING && COMBINED.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });
});
