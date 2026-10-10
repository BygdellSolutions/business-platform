import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * A unit is free text ("pcs", "h", but also "1"), so a quantity printed with the unit right after it can read as one odd
 * number ("10 1"). Units go in their own column or are named ("unit: pcs"). This scan refuses the adjacent form.
 */

const ROOT = path.resolve(__dirname, "..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "node_modules" || name.startsWith(".") ? [] : files(full);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [full] : [];
  });
}

// A closing figure (`trimQuantity(...)}` or `<DecimalText ... />`) followed by a unit expression.
const ADJACENT = /(\)\}|\/>)\s*\{[\w.]*unit\}/;

describe("units", () => {
  it("are never printed right after a quantity", () => {
    const offenders = ["app", "features", "components"]
      .flatMap((dir) => files(path.join(ROOT, dir)))
      .flatMap((file) =>
        readFileSync(file, "utf-8")
          .split("\n")
          .map((line, index) => ({ line, index }))
          .filter(({ line }) => ADJACENT.test(line))
          .map(({ index }) => `${path.relative(ROOT, file)}:${index + 1}`),
      );
    expect(offenders).toEqual([]);
  });
});
