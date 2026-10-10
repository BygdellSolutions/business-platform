import Link from "next/link";

import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { SortHeader } from "@/components/ui/SortHeader";
import { readActiveRole } from "@/lib/active-role";
import type { Supplier } from "@/lib/api/types";
import { backendQuery, listHref, sortHref, pageOf, parseListParams } from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";

const SORTS = ["number", "name", "contact_person", "email", "phone", "active"] as const;

/** Suppliers as FastAPI lists them; the filters live in the address (a plain GET form sets them). */
export default async function SuppliersPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const canWrite = canWriteRecords(await readActiveRole(orgId));
  const list = parseListParams(await searchParams, [], [], {}, SORTS);
  const { rows: suppliers, hasNext } = pageOf(await serverRead<Supplier[]>(orgId, "/api/suppliers", backendQuery(list)));
  const base = `/o/${orgId}/suppliers`;
  const filtered = list.q !== "" || list.active !== "all";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Suppliers</h1>
        {canWrite && (
          <Link href={`${base}/new`} className="underline" data-testid="new-supplier">
            New supplier
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list} />

      {suppliers.length === 0 ? (
        <p data-testid="empty">{filtered ? "No suppliers match." : "No suppliers yet."}</p>
      ) : (
        <table data-testid="suppliers-table" className="w-full max-w-4xl text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <SortHeader label="No." sortKey="number" current={list.sort} dir={list.dir} href={sortHref(base, list, "number")} align="right" />
              <SortHeader label="Name" sortKey="name" current={list.sort} dir={list.dir} href={sortHref(base, list, "name")} />
              <SortHeader label="Contact person" sortKey="contact_person" current={list.sort} dir={list.dir} href={sortHref(base, list, "contact_person")} />
              <SortHeader label="Email" sortKey="email" current={list.sort} dir={list.dir} href={sortHref(base, list, "email")} />
              <SortHeader label="Phone" sortKey="phone" current={list.sort} dir={list.dir} href={sortHref(base, list, "phone")} />
              <SortHeader label="Status" sortKey="active" current={list.sort} dir={list.dir} href={sortHref(base, list, "active")} last />
            </tr>
          </thead>
          <tbody>
            {suppliers.map((supplier) => (
              <tr key={supplier.id} data-testid="supplier-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4 text-right" data-testid="record-number">
                  {supplier.number}
                </td>
                <td className="py-1 pr-4">
                  <Link href={`${base}/${supplier.id}`} className="underline">
                    {supplier.name}
                  </Link>
                </td>
                <td className="py-1 pr-4">{supplier.contact_person}</td>
                <td className="py-1 pr-4">{supplier.email}</td>
                <td className="py-1 pr-4">{supplier.phone}</td>
                <td className="py-1">
                  <StatusBadge active={supplier.active} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Pagination page={list.page} hasNext={hasNext} hrefFor={(page) => listHref(base, list, { page })} />
    </div>
  );
}
