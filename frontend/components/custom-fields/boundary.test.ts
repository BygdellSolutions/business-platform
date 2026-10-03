// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The Custom Fields frontend is GENERIC: it understands field metadata and six value types, and
 * nothing about what any module uses them for. This test keeps it that way, mechanically.
 *
 *   1. It may import only generic layers: React, Next's router, the shared UI primitives, the
 *      shared `lib` code and itself. It must NOT import any `@/features/...` (customers, horses,
 *      sales, ...) or any other module's code.
 *   2. It must not decide anything by comparing metadata to a literal: no `key === "..."`, no
 *      `source === "..."`, no `entity_type === "..."`, no switch on them. The type of a field
 *      (text, number, date, boolean, select, reference) is the only thing it may branch on.
 *   3. It must not mention domain things at all (the words below), in code or comments, so the
 *      generic layer cannot grow a special case "just for" one use.
 *
 * Tests of the layer (`*.test.*`) are exempt: they build synthetic metadata on purpose.
 */

const ROOT = path.resolve(__dirname, "..", "..");
const LAYERS = ["components/custom-fields", "lib/custom-fields"];

const ALLOWED_IMPORT = [
  /^react$/,
  /^react\/.+/,
  /^next\/navigation$/,
  /^server-only$/,
  /^@\/components\/ui\//,
  /^@\/components\/custom-fields\//,
  /^@\/components\/shell\/org-context$/, // which organization the page is for: infrastructure, not a module
  /^@\/lib\//, // shared helpers (and this layer's own lib)
  /^\.\.?\//,
];

// A hyphen joins words ("items-center" is a CSS class, not the domain word "items").
const DOMAIN_WORDS = /(?<![\w-])(horse|horses|equine|owner|customer|customers|catalog|item|items|stable|billing|sales|transaction|transactions|invoice|invoices|animal|animals)(?![\w-])/i;
const COMPARES_METADATA_TO_LITERAL = [
  /\.(key|source|entity_type|depends_on|filter|reference_source)\s*[!=]==?\s*["'`]/,
  /["'`]\s*[!=]==?\s*[\w.]*\.(key|source|entity_type|depends_on|filter|reference_source)\b/,
  /\bswitch\s*\(\s*[\w.?]*\.(key|source|entity_type|depends_on|filter|reference_source)\s*\)/,
];

function imports(source: string): string[] {
  const found = new Set<string>();
  for (const match of source.matchAll(/\bfrom\s+["']([^"']+)["']/g)) found.add(match[1]);
  for (const match of source.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g)) found.add(match[1]);
  for (const match of source.matchAll(/^\s*import\s+["']([^"']+)["']/gm)) found.add(match[1]);
  return [...found];
}

/** What is wrong with this source, as readable messages. Empty if it is generic. */
export function violations(source: string): string[] {
  const problems: string[] = [];
  for (const specifier of imports(source)) {
    if (!ALLOWED_IMPORT.some((pattern) => pattern.test(specifier))) problems.push(`imports "${specifier}", which is not a generic layer`);
  }
  for (const pattern of COMPARES_METADATA_TO_LITERAL) {
    const hit = pattern.exec(source);
    if (hit) problems.push(`decides by comparing metadata to a literal: ${hit[0].trim()}`);
  }
  const word = DOMAIN_WORDS.exec(source);
  if (word) problems.push(`mentions the domain word "${word[0]}"`);
  return problems;
}

function sourceFiles(directory: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = path.join(directory, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

describe("the generic Custom Fields layer", () => {
  const files = LAYERS.flatMap((layer) => sourceFiles(path.join(ROOT, layer)));

  it("exists, and covers both the components and the library", () => {
    const relative = files.map((file) => path.relative(ROOT, file).replaceAll("\\", "/"));
    expect(relative).toEqual(expect.arrayContaining(["components/custom-fields/CustomFieldsPanel.tsx", "components/custom-fields/CustomFieldControl.tsx", "lib/custom-fields/model.ts", "lib/custom-fields/api.ts"]));
  });

  it.each(files.map((file) => [path.relative(ROOT, file).replaceAll("\\", "/"), file]))("%s imports only generic layers, compares no metadata to literals, and names no domain", (_name, file) => {
    expect(violations(readFileSync(file, "utf8"))).toEqual([]);
  });
});

describe("the boundary check itself (so a weak check cannot pass silently)", () => {
  it.each([
    ['import { Horse } from "@/features/horses/HorseForm";', "@/features/horses/HorseForm"],
    ['import { customerSearch } from "@/features/customers/customer-picker";', "@/features/customers"],
    ['import x from "@/features/transactions/editor-context";', "@/features/transactions"],
    ['const m = await import("@/features/anything/else");', "@/features/anything"],
    ['import "@/features/side-effect";', "@/features/side-effect"],
    ['import { b } from "@/components/shell/nav";', "@/components/shell/nav"],
    ['import { c } from "@/app/o/[orgId]/page";', "@/app/o"],
  ])("rejects an import of %#", (source, fragment) => {
    expect(violations(source).join(" ")).toContain(fragment);
  });

  it.each([
    'if (field.key === "owner") return 1;',
    "if (definition.key !== 'horse') return 1;",
    'if (field.reference.source === "customer") return 1;',
    'return definition.reference_source == "horse";',
    'if (d.entity_type === `transaction_line`) {}',
    'if ("owner" === definition.key) {}',
    "switch (definition.key) { case 'a': break; }",
    "switch (definition.reference?.source) { case 'a': break; }",
  ])("rejects metadata compared to a literal: %s", (source) => {
    expect(violations(source).join(" ")).toMatch(/comparing metadata to a literal/);
  });

  it.each(["// Owner -> Horse", "const label = 'Horse';", "/* customers */", "the Catalog", "stable", "an invoice line"])("rejects the domain word in %j", (source) => {
    expect(violations(source).join(" ")).toMatch(/domain word/);
  });

  it("accepts what the layer legitimately does", () => {
    const fine = `
      import { useState } from "react";
      import { EntityPicker } from "@/components/ui/EntityPicker";
      import { applyChange } from "@/lib/custom-fields/model";
      import { x } from "./sibling";
      switch (definition.field_type) { case "text": break; case "reference": break; }
      if (definition.field_type === "boolean") {}
      const parent = definitions.find((candidate) => candidate.key === parentKey);
      if (definition.key === definition.reference?.depends_on) {}
    `;
    expect(violations(fine)).toEqual([]);
  });
});
