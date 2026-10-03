import type { ReactNode } from "react";

import type { ListParams } from "@/lib/list-params";

const CONTROL = "rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900";

/**
 * Search and filters as a plain GET form: submitting it loads the list address with the
 * chosen parameters, so the URL (not hidden client state) is what the page shows. Works
 * without any client JavaScript. Changing a filter starts again at page 1.
 */
export function ListFilters({
  action,
  params,
  children,
  search = true,
  activeStatus = true,
}: {
  action: string;
  params: ListParams;
  children?: ReactNode;
  /** The free-text search box (a list whose backend has no `q` turns it off). */
  search?: boolean;
  /** The Active/Inactive filter (records with a lifecycle status of their own turn it off). */
  activeStatus?: boolean;
}) {
  return (
    <form action={action} method="get" role="search" aria-label="Filter" className="flex flex-wrap items-end gap-3">
      {search && (
        <label className="flex flex-col gap-1 text-sm">
          Search
          <input name="q" type="search" defaultValue={params.q} maxLength={255} className={CONTROL} />
        </label>
      )}
      {children}
      {activeStatus && (
        <label className="flex flex-col gap-1 text-sm">
          Status
          <select name="active" defaultValue={params.active} className={CONTROL}>
            <option value="all">All</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </label>
      )}
      <button type="submit" className="rounded border border-zinc-400 px-3 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800">
        Apply
      </button>
      {/* A plain link: clearing the filters is just the bare list address. */}
      <a href={action} className="text-sm underline">
        Clear
      </a>
    </form>
  );
}
