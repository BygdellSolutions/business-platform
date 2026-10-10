import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * The owner reported rows that broke onto two lines (the catalog's stock badges, then the invoice list's status
 * badges). Rule: a main list keeps each row on one line (`whitespace-nowrap` on the table) and scrolls sideways
 * (`overflow-x-auto` around it) instead of squeezing its cells.
 */
const LISTS = ["catalog", "customers", "suppliers", "horses", "transactions", "invoices"];

describe("main lists", () => {
  it.each(LISTS)("%s keeps every row on one line", (list) => {
    const source = readFileSync(join("app", "o", "[orgId]", list, "page.tsx"), "utf8");
    const table = /<table\b[^>]*>/.exec(source)?.[0] ?? "";
    expect(table).toContain("whitespace-nowrap");
    expect(source).toContain('className="overflow-x-auto"');
  });
});
