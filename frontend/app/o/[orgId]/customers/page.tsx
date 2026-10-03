import Link from "next/link";

import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Customer } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams } from "@/lib/list-params";
import { serverRead } from "@/lib/server-api";

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
  const list = parseListParams(await searchParams);
  const { rows: customers, hasNext } = pageOf(await serverRead<Customer[]>(orgId, "/api/customers", backendQuery(list)));
  const base = `/o/${orgId}/customers`;
  const filtered = list.q !== "" || list.active !== "all";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Customers</h1>
        <Link href={`${base}/new`} className="underline" data-testid="new-customer">
          New customer
        </Link>
      </div>

      <ListFilters action={base} params={list} />

      {customers.length === 0 ? (
        <p data-testid="empty">{filtered ? "No customers match." : "No customers yet."}</p>
      ) : (
        <table data-testid="customers-table" className="w-full max-w-4xl text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Name</th>
              <th className="py-1 pr-4">Type</th>
              <th className="py-1 pr-4">Email</th>
              <th className="py-1 pr-4">Phone</th>
              <th className="py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {customers.map((customer) => (
              <tr key={customer.id} data-testid="customer-row" className="border-b border-zinc-200 dark:border-zinc-800">
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
