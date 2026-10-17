import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" || name === ".next" ? [] : sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

/** A link that leaves the page ("Back to orders", "Cancel") is a button like "New order", not bare underlined text (reported as inconsistent). */
describe("back and cancel links", () => {
  it("use SECONDARY_LINK, not bare underlined text", () => {
    const offenders = ["components", "features", "app"]
      .flatMap(sources)
      // CustomerOrders' "All orders of this customer" is a pager-style link under a table, not a page action.
      .filter((file) => !file.endsWith("CustomerOrders.tsx") && readFileSync(file, "utf8").includes('className="text-sm underline"'));
    expect(offenders).toEqual([]);
  });
});
