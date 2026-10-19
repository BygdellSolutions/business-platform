import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** A button that backs out of a question must say where it goes: a bare "Keep" was reported as unclear (what is kept?). */
function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" || name === ".next" ? [] : sources(path);
    return /\.tsx$/.test(name) && !/\.test\.tsx$/.test(name) ? [path] : [];
  });
}

describe("buttons that back out", () => {
  it("are never labelled just 'Keep'", () => {
    const offenders = ["components", "features", "app"].flatMap(sources).filter((file) => />\s*Keep\s*</.test(readFileSync(file, "utf8")) || /\{"Keep"\}/.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });
});
