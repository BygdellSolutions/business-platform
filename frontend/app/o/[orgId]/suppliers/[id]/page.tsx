import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { Notice } from "@/components/ui/Notice";
import { SupplierDetails } from "@/features/suppliers/SupplierDetails";
import { SupplierForm } from "@/features/suppliers/SupplierForm";
import { readActiveRole } from "@/lib/active-role";
import type { Incoming, Organization, Supplier } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";
import { readRecordHistory } from "@/lib/history-server";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";

const DELIVERY_STATES: Record<Incoming["state"], string> = {
  expected: "Expected",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

/** Another organization's supplier, a random id and a malformed id all end in the same generic not-found page. */
export default async function SupplierPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string; id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId, id } = await params;
  const { created } = await searchParams;
  const recordId = requireUuid(id);
  const [supplier, role, organization, history, deliveries] = await Promise.all([
    serverRead<Supplier>(orgId, `/api/suppliers/${recordId}`),
    readActiveRole(orgId),
    serverRead<Organization>(orgId, "/api/organization"),
    readRecordHistory(orgId, "supplier", recordId),
    serverRead<Incoming[]>(orgId, "/api/inventory/incoming", `?${new URLSearchParams({ supplier_id: recordId, open_only: "false" })}`),
  ]);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold" data-testid="record-name">
          {supplier.name}
        </h1>
        <Link href={`/o/${orgId}/suppliers`} className="text-sm underline">
          Back to suppliers
        </Link>
      </div>
      {created === "1" && <Notice testId="created">Supplier created.</Notice>}
      <RecordMeta record={supplier} people={history.history.people} timeZone={organization.timezone} />
      {canWriteRecords(role) ? <SupplierForm key={supplier.id} supplier={supplier} /> : <SupplierDetails supplier={supplier} />}
      <CollapsibleSection title="Deliveries from this supplier" count={deliveries.length} testId="supplier-deliveries-section" toggleTestId="deliveries-toggle">
        {deliveries.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-deliveries">
            None yet. Choose this supplier when you record incoming stock on a product.
          </p>
        ) : (
          <table className="text-left text-sm" data-testid="supplier-deliveries">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Product</th>
                <th className="py-1 pr-4">Unit</th>
                <th className="py-1 pr-4 text-right">Ordered</th>
                <th className="py-1 pr-4 text-right">Received</th>
                <th className="py-1 pr-4">Expected</th>
                <th className="py-1 pr-4">Reference</th>
                <th className="py-1">State</th>
              </tr>
            </thead>
            <tbody>
              {deliveries.map((row) => (
                <tr key={row.id} data-testid="supplier-delivery" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/catalog/${row.item_id}`} className="underline">
                      {row.item_name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{row.item_unit}</td>
                  <td className="py-1 pr-4 text-right">{trimQuantity(row.quantity)}</td>
                  <td className="py-1 pr-4 text-right">{trimQuantity(row.received)}</td>
                  <td className="py-1 pr-4">{row.expected_on ?? "—"}</td>
                  <td className="py-1 pr-4">{row.reference}</td>
                  <td className="py-1">{DELIVERY_STATES[row.state]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>
      <RecordHistory data={history} entityType="supplier" timeZone={organization.timezone} />
    </div>
  );
}
