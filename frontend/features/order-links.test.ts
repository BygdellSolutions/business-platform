import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A link to an order is named by the ORDER NUMBER ("Order no. 1004"), never by the order's date: a column headed "Order" that showed
 * the date was a reported bug. This reads every screen's source and fails for a link to an order whose text is a date.
 */
const ROOTS = ["features", "components", "app"];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" || name === ".next" ? [] : sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

describe("links to orders", () => {
  it("are never written as a date", () => {
    const offenders: string[] = [];
    for (const file of ROOTS.flatMap((root) => sources(root))) {
      const text = readFileSync(file, "utf8");
      const links = text.matchAll(/<Link\s+href=\{`[^`]*\/transactions\/\$\{[^}]*\}`\}[^>]*>([\s\S]*?)<\/Link>/g);
      // (an old invoice whose order had no number falls back to "Order of <date>": that text also names the number, so it passes)
      for (const link of links) if (/transaction_date|\bdate\b/.test(link[1]) && !/transaction_number|\bnumber\b/.test(link[1])) offenders.push(`${file}: ${link[1].trim().slice(0, 60)}`);
    }
    expect(offenders).toEqual([]);
  });

  it("is able to see the pattern it guards against (the matcher is not blind)", () => {
    const bad = "<Link href={`/o/${orgId}/transactions/${service.transaction_id}`} className=\"underline\">\n  {service.transaction_date}\n</Link>";
    expect([...bad.matchAll(/<Link\s+href=\{`[^`]*\/transactions\/\$\{[^}]*\}`\}[^>]*>([\s\S]*?)<\/Link>/g)]).toHaveLength(1);
  });
});
