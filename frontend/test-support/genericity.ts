import { readdirSync, statSync } from "node:fs";
import path from "node:path";

/**
 * The mechanical check behind "this layer is GENERIC" (test-only; used by the boundary tests of
 * the custom-fields layer and of the read-only snapshot renderer).
 *
 *   1. It may import only generic layers: React, Next's router, shared UI primitives, shared `lib`
 *      code and itself. No `@/features/...`, no other module's code.
 *   2. It must not decide anything by comparing metadata to a literal (`key === "..."`).
 *   3. It must not mention domain things at all, in code or comments.
 */

const BASE_ALLOWED_IMPORT = [
  /^react$/,
  /^react\/.+/,
  /^next\/navigation$/,
  /^server-only$/,
  /^@\/components\/ui\//,
  /^@\/components\/shell\/org-context$/, // which organization the page is for: infrastructure, not a module
  /^@\/lib\//, // shared helpers
  /^\.\.?\//,
];

// A hyphen joins words ("items-center" is a CSS class, not the domain word "items").
export const DOMAIN_WORDS = /(?<![\w-])(horse|horses|equine|owner|customer|customers|catalog|item|items|stable|billing|sales|transaction|transactions|invoice|invoices|animal|animals)(?![\w-])/i;
export const COMPARES_METADATA_TO_LITERAL = [
  /\.(key|source|entity_type|depends_on|filter|reference_source)\s*[!=]==?\s*["'`]/,
  /["'`]\s*[!=]==?\s*[\w.]*\.(key|source|entity_type|depends_on|filter|reference_source)\b/,
  /\bswitch\s*\(\s*[\w.?]*\.(key|source|entity_type|depends_on|filter|reference_source)\s*\)/,
];

export function imports(source: string): string[] {
  const found = new Set<string>();
  for (const match of source.matchAll(/\bfrom\s+["']([^"']+)["']/g)) found.add(match[1]);
  for (const match of source.matchAll(/\bimport\s*\(\s*["']([^"']+)["']\s*\)/g)) found.add(match[1]);
  for (const match of source.matchAll(/^\s*import\s+["']([^"']+)["']/gm)) found.add(match[1]);
  return [...found];
}

/** What is wrong with this source, as readable messages. `ownLayers` are import patterns of the layer itself. Empty if generic. */
export function violationsOf(source: string, ownLayers: RegExp[] = []): string[] {
  const allowed = [...BASE_ALLOWED_IMPORT, ...ownLayers];
  const problems: string[] = [];
  for (const specifier of imports(source)) {
    if (!allowed.some((pattern) => pattern.test(specifier))) problems.push(`imports "${specifier}", which is not a generic layer`);
  }
  for (const pattern of COMPARES_METADATA_TO_LITERAL) {
    const hit = pattern.exec(source);
    if (hit) problems.push(`decides by comparing metadata to a literal: ${hit[0].trim()}`);
  }
  const word = DOMAIN_WORDS.exec(source);
  if (word) problems.push(`mentions the domain word "${word[0]}"`);
  return problems;
}

export function sourceFiles(directory: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(directory)) {
    const full = path.join(directory, entry);
    if (statSync(full).isDirectory()) out.push(...sourceFiles(full));
    else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}
