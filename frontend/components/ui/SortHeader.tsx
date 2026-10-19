import Link from "next/link";

import type { SortDir } from "@/lib/list-params";

/**
 * A column heading that sorts its table: a plain link to the sorted address (no client JavaScript), with the current
 * direction shown and announced (`aria-sort`). `href` is the address that applies this column's next order.
 */
export function SortHeader({
  label,
  sortKey,
  current,
  dir,
  href,
  onSort,
  align = "left",
  last = false,
  title,
}: {
  label: string;
  sortKey: string;
  /** The key the table is sorted by now ("" for its own order). */
  current: string;
  dir: SortDir;
  /** The address that applies this column's next order (a server-rendered table)... */
  href?: string;
  /** ...or what a click does (a client table sorting in place). Neither: a plain heading. */
  onSort?: () => void;
  align?: "left" | "right";
  /** The last column has no right padding. */
  last?: boolean;
  title?: string;
}) {
  const sortable = href !== undefined || onSort !== undefined;
  const active = sortable && current === sortKey;
  const content = (
    <>
      {label}
      <span aria-hidden="true" className={active ? "" : "text-zinc-400"}>
        {active ? (dir === "asc" ? " ▲" : " ▼") : " ↕"}
      </span>
    </>
  );
  return (
    <th
      className={`py-1 ${last ? "" : "pr-4"} ${align === "right" ? "text-right" : ""}`}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      title={title}
    >
      {href !== undefined ? (
        <Link href={href} scroll={false} className="hover:underline" data-testid={`sort-${sortKey}`}>
          {content}
        </Link>
      ) : onSort !== undefined ? (
        <button type="button" onClick={onSort} className="font-[inherit] hover:underline" data-testid={`sort-${sortKey}`}>
          {content}
        </button>
      ) : (
        label
      )}
    </th>
  );
}
