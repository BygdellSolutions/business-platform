import Link from "next/link";

import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { SortHeader } from "@/components/ui/SortHeader";
import { readActiveRole } from "@/lib/active-role";
import type { Customer } from "@/lib/api/types";
import { backendQuery, listHref, sortHref, pageOf, parseListParams } from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";

const SORTS = ["number", "name", "type", "email", "phone", "active"] as const;

/**
 * The initial read happens on the server: the organization comes from the URL, FastAPI decides
 * what it may see, and the filters live in the address (a plain GET form sets them).
 */
export default async function CustomersPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const canWrite = canWriteRecords(await readActiveRole(orgId));
  const list = parseListParams(await searchParams, [], [], {}, SORTS);
  const { rows: customers, hasNext } = pageOf(await serverRead<Customer[]>(orgId, "/api/customers", backendQuery(list)));
  const base = `/o/${orgId}/customers`;
  const filtered = list.q !== "" || list.active !== "all";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Customers</h1>
        {canWrite && (
          <Link href={`${base}/new`} className="underline" data-testid="new-customer">
            New customer
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list} />

      {customers.length === 0 ? (
        <p data-testid="empty">{filtered ? "No customers match." : "No customers yet."}</p>
      ) : (
        <table data-testid="customers-table" className="w-full max-w-4xl text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <SortHeader label="No." sortKey="number" current={list.sort} dir={list.dir} href={sortHref(base, list, "number")} align="right" />
              <SortHeader label="Name" sortKey="name" current={list.sort} dir={list.dir} href={sortHref(base, list, "name")} />
              <SortHeader label="Type" sortKey="type" current={list.sort} dir={list.dir} href={sortHref(base, list, "type")} />
              <SortHeader label="Email" sortKey="email" current={list.sort} dir={list.dir} href={sortHref(base, list, "email")} />
              <SortHeader label="Phone" sortKey="phone" current={list.sort} dir={list.dir} href={sortHref(base, list, "phone")} />
              <SortHeader label="Status" sortKey="active" current={list.sort} dir={list.dir} href={sortHref(base, list, "active")} last />
            </tr>
          </thead>
          <tbody>
            {customers.map((customer) => (
              <tr key={customer.id} data-testid="customer-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4 text-right" data-testid="record-number">
                  {customer.number}
                </td>
                <td className="py-1 pr-4">
                  <Link href={`${base}/${customer.id}`} className="underline">
                    {customer.name}
                  </Link>
                </td>
                <td className="py-1 pr-4">{customer.customer_type}</td>
                <td className="py-1 pr-4">{customer.email}</td>
                <td className="py-1 pr-4">{customer.phone}</td>
                <td className="py-1">
                  <StatusBadge active={customer.active} />
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
