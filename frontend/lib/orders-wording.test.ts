import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

/**
 * People read "order", never "transaction" (decided 2026-10-09). URLs, API paths, record keys and test ids keep
 * "transaction"; what is shown does not. This scan reads only text: string literals, JSX text and lines of plain
 * words, outside comments, and flags the word when it stands in a phrase.
 */

const ROOT = path.resolve(__dirname, "..");

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return name === "node_modules" || name.startsWith(".") ? [] : files(full);
    return /\.(tsx|ts)$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== "testing.tsx" ? [full] : [];
  });
}

const COMMENT_LINE = /^\s*(\/\/|\*|\/\*)/;
const PLAIN_WORDS = /^\s*[A-Za-z][^=;{}()<>"'`:]*$/;
const WORD = /(^|[\s(])[Tt]ransactions?(?=$|[\s.,!?:;)])/;

/** The pieces of a line that people can read. */
function texts(raw: string): string[] {
  if (COMMENT_LINE.test(raw)) return [];
  const line = raw
    .replace(/\{\/\*.*?\*\/\}/g, "")
    .replace(/\/\*.*?\*\//g, "")
    .replace(/\s\/\/\s.*$/, "")
    .replace(/&\w+;/g, ""); // an entity such as &apos; is part of the text, not a quote
  const literals = [...line.matchAll(/"([^"]*)"|'([^']*)'|`([^`]*)`/g)].map((m) => m[1] ?? m[2] ?? m[3] ?? "");
  const jsx = [...line.matchAll(/>([^<>{}]+)</g)].map((m) => m[1]);
  return [...literals, ...jsx, ...(PLAIN_WORDS.test(line) ? [line] : [])];
}

function proseLines(source: string): number[] {
  return source
    .split("\n")
    .map((line, index) => ({ line, index }))
    .filter(({ line }) => texts(line).some((text) => text.trim().includes(" ") && WORD.test(text)))
    .map(({ index }) => index + 1);
}

describe("wording", () => {
  it("says order, never transaction, in what people read", () => {
    const offenders = ["app", "features", "components", "lib"]
      .flatMap((dir) => files(path.join(ROOT, dir)))
      .flatMap((file) => proseLines(readFileSync(file, "utf-8")).map((line) => `${path.relative(ROOT, file)}:${line}`));
    expect(offenders).toEqual([]);
  });

  it("catches the word in text and messages, not in names, paths or comments", () => {
    expect(proseLines("<p>Select one or more completed transactions.</p>")).toEqual([1]);
    expect(proseLines('  const message = "A transaction cannot be changed";')).toEqual([1]);
    expect(proseLines("            Customers, the catalog, transactions, issued invoices and every member&apos;s access")).toEqual([1]);
    expect(proseLines("<Link href={`/o/${orgId}/transactions`}>Orders</Link>")).toEqual([]);
    expect(proseLines("  const transaction = await read(id);")).toEqual([]);
    expect(proseLines('readRecordHistory(orgId, "transaction", recordId)')).toEqual([]);
    expect(proseLines('<tr data-testid="transaction-row">')).toEqual([]);
    expect(proseLines("  transaction: Transaction;")).toEqual([]);
    expect(proseLines("  router.refresh(); // without the new transaction")).toEqual([]);
    expect(proseLines("{/* Never guessed: a transaction that predates currencies says so. */}")).toEqual([]);
  });
});
