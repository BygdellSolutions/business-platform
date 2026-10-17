import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * A server component that imports a VALUE other than a component from a "use client" module gets a client reference,
 * not the value: a label table read that way is silently empty (the Inventory page's backorder states once were).
 * Server files may import only components (PascalCase) and types from client modules; shared values live in plain
 * modules.
 */

const ROOT = path.resolve(__dirname, "..");
const COMPONENT = /^[A-Z][a-z][A-Za-z0-9]*$/;

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "node_modules" || name.startsWith(".") ? [] : files(full);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [full] : [];
  });
}

function resolve(specifier: string): string | null {
  if (!specifier.startsWith("@/")) return null;
  const base = path.join(ROOT, specifier.slice(2));
  for (const candidate of [`${base}.tsx`, `${base}.ts`, path.join(base, "index.ts")]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      /* not this one */
    }
  }
  return null;
}

const isClient = (source: string) => /^\s*["']use client["']/.test(source);

describe("server files import only components from client modules", () => {
  it("has no value imports across the boundary", () => {
    const offenders: string[] = [];
    for (const file of files(path.join(ROOT, "app"))) {
      const source = readFileSync(file, "utf-8");
      if (isClient(source)) continue;
      for (const match of source.matchAll(/^import\s+(type\s+)?\{([^}]*)\}\s+from\s+"([^"]+)";/gm)) {
        if (match[1]) continue; // a type-only import is erased
        const target = resolve(match[3]);
        if (target === null || !isClient(readFileSync(target, "utf-8"))) continue;
        for (const raw of match[2].split(",")) {
          const name = raw.trim().replace(/^type\s+/, "");
          if (name && !raw.trim().startsWith("type ") && !COMPONENT.test(name)) offenders.push(`${path.relative(ROOT, file)} imports ${name} from ${match[3]}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
