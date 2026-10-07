import Link from "next/link";

import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CustomerFilter } from "@/features/customers/CustomerFilter";
import { readActiveRole } from "@/lib/active-role";
import type { Customer, CustomerRef, Horse } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams } from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

const REFS = ["owner_customer_id", "stable_customer_id"] as const;

/** Name of the customer chosen in a filter. A foreign and a random id are both unknown, and look the same. */
async function filterEntity(orgId: string, id: string | undefined) {
  if (id === undefined) return null;
  const customer = await serverReadOrNull<Customer>(orgId, `/api/customers/${id}`);
  return customer === null ? { id, label: "Unknown customer" } : { id, label: customer.name, inactive: !customer.active };
}

function CustomerLink({ orgId, customer }: { orgId: string; customer: CustomerRef | null }) {
  if (customer === null) return null;
  return (
    <>
      <Link href={`/o/${orgId}/customers/${customer.id}`} className="underline">
        {customer.name}
      </Link>
      {!customer.active && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
    </>
  );
}

export default async function HorsesPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const canWrite = canWriteRecords(await readActiveRole(orgId));
  const list = parseListParams(await searchParams, [], REFS);
  const [rows, owner, stable] = await Promise.all([
    serverRead<Horse[]>(orgId, "/api/horses", backendQuery(list)),
    filterEntity(orgId, list.refs.owner_customer_id),
    filterEntity(orgId, list.refs.stable_customer_id),
  ]);
  const { rows: horses, hasNext } = pageOf(rows);
  const base = `/o/${orgId}/horses`;
  const filtered = list.q !== "" || list.active !== "all" || Object.keys(list.refs).length > 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Horses</h1>
        {canWrite && (
          <Link href={`${base}/new`} className="underline" data-testid="new-horse">
            New horse
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list}>
        <CustomerFilter key={`owner-${list.refs.owner_customer_id ?? ""}`} name="owner_customer_id" label="Owner" initial={owner} />
        <CustomerFilter key={`stable-${list.refs.stable_customer_id ?? ""}`} name="stable_customer_id" label="Stable" initial={stable} />
      </ListFilters>

      {horses.length === 0 ? (
        <p data-testid="empty">{filtered ? "No horses match." : "No horses yet."}</p>
      ) : (
        <table data-testid="horses-table" className="w-full max-w-5xl text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Name</th>
              <th className="py-1 pr-4">Owner</th>
              <th className="py-1 pr-4">Stable</th>
              <th className="py-1 pr-4">Birth year</th>
              <th className="py-1 pr-4">Sex</th>
              <th className="py-1 pr-4">Breed</th>
              <th className="py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {horses.map((horse) => (
              <tr key={horse.id} data-testid="horse-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4">
                  <Link href={`${base}/${horse.id}`} className="underline">
                    {horse.name}
                  </Link>
                </td>
                <td className="py-1 pr-4" data-testid="horse-owner">
                  <CustomerLink orgId={orgId} customer={horse.owner} />
                </td>
                <td className="py-1 pr-4" data-testid="horse-stable">
                  <CustomerLink orgId={orgId} customer={horse.stable} />
                </td>
                <td className="py-1 pr-4">{horse.birth_year}</td>
                <td className="py-1 pr-4">{horse.sex}</td>
                <td className="py-1 pr-4">{horse.breed}</td>
                <td className="py-1">
                  <StatusBadge active={horse.active} />
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
