import { compareDecimal } from "@/lib/decimal";
import type { SortDir } from "@/lib/list-params";

/**
 * Sorting a table whose rows are all on the page (a record's deliveries, a customer's orders, members...). The order
 * lives in the address, one pair of parameters per table (`<name>` and `<name>_dir`), so a page with several tables
 * sorts each on its own and reloading or sharing the link keeps it. Client tables sort in place instead
 * (`use-sorted-rows.ts`); the paged lists sort on the server (`list-params.ts`), because a sort must cover every page.
 */

type RawParams = Record<string, string | string[] | undefined>;

/** Plain data (a server page hands it to a client table): the current order and, per column, the address that
 * sorts by it (ascending first, then turned around), keeping everything else in the address. */
export interface TableSort {
  sort: string;
  dir: SortDir;
  hrefs: Record<string, string>;
}

export function tableSort(raw: RawParams, base: string, keys: readonly string[], param = "sort"): TableSort {
  const requested = typeof raw[param] === "string" ? raw[param] : "";
  const sort = keys.includes(requested) ? requested : "";
  const dir: SortDir = sort !== "" && raw[`${param}_dir`] === "desc" ? "desc" : "asc";
  const kept = Object.entries(raw).filter((entry): entry is [string, string] => typeof entry[1] === "string" && entry[0] !== param && entry[0] !== `${param}_dir`);
  const href = (key: string) => {
    const query = new URLSearchParams(kept);
    query.set(param, key);
    if (sort === key && dir === "asc") query.set(`${param}_dir`, "desc");
    return `${base}?${query.toString()}`;
  };
  return { sort, dir, hrefs: Object.fromEntries(keys.map((key) => [key, href(key)])) };
}

/** What a column sorts by: text (compared without case), a decimal string (exactly), a plain number, or nothing. */
export type SortValue = { text: string | null } | { decimal: string | null } | { number: number | null };

/** The rows in the table's order: empty values last either way; equal values keep their order (stable). */
export function sortRows<T>(rows: readonly T[], sort: { sort: string; dir: SortDir }, columns: Record<string, (row: T) => SortValue>): T[] {
  const value = columns[sort.sort];
  if (!value) return [...rows];
  const plain = (cell: SortValue) => ("text" in cell ? cell.text : "decimal" in cell ? cell.decimal : cell.number);
  const present = rows.filter((row) => plain(value(row)) !== null && plain(value(row)) !== "");
  const empty = rows.filter((row) => !present.includes(row));
  const compare = (a: SortValue, b: SortValue): number => {
    if ("decimal" in a && "decimal" in b) return compareDecimal(a.decimal as string, b.decimal as string);
    if ("number" in a && "number" in b) return (a.number as number) - (b.number as number);
    return String(plain(a)).localeCompare(String(plain(b)), undefined, { sensitivity: "base", numeric: true });
  };
  const ordered = [...present].sort((x, y) => compare(value(x), value(y)) * (sort.dir === "desc" ? -1 : 1));
  return [...ordered, ...empty];
}
