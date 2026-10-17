import { useMemo, useState } from "react";

import type { SortDir } from "@/lib/list-params";
import { sortRows, type SortValue } from "@/lib/table-sort";

/**
 * A client table that sorts its rows in place: click a heading for ascending, again for descending. Display only
 * (nothing is sent or stored); the rows are the ones the table already has. `header(key)` is what SortHeader needs.
 * Keep `columns` stable (a module constant), so the sort is not recomputed on every render.
 */
export function useSortedRows<T>(rows: readonly T[], columns: Record<string, (row: T) => SortValue>) {
  const [order, setOrder] = useState<{ sort: string; dir: SortDir }>({ sort: "", dir: "asc" });
  const sorted = useMemo(() => sortRows(rows, order, columns), [rows, order, columns]);
  const header = (key: string) => ({
    sortKey: key,
    current: order.sort,
    dir: order.dir,
    onSort: () => setOrder((now) => ({ sort: key, dir: now.sort === key && now.dir === "asc" ? "desc" : "asc" })),
  });
  return { rows: sorted, header };
}
