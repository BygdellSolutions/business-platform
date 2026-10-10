import Link from "next/link";

import { RecordHistory } from "@/components/history/RecordHistory";
import { RecordMeta } from "@/components/history/RecordMeta";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { Notice } from "@/components/ui/Notice";
import { SupplierDetails } from "@/features/suppliers/SupplierDetails";
import { SupplierForm } from "@/features/suppliers/SupplierForm";
import { readActiveRole } from "@/lib/active-role";
import type { Incoming, Organization, Supplier } from "@/lib/api/types";
import { DecimalText } from "@/components/ui/DecimalText";
import { trimQuantity } from "@/lib/decimal";
import { readRecordHistory } from "@/lib/history-server";
import { formatDay } from "@/lib/timestamps";
import { canWriteRecords } from "@/lib/roles";
import { requireUuid, serverRead } from "@/lib/server-api";
import { SortHeader } from "@/components/ui/SortHeader";
import { sortRows, tableSort, type SortValue } from "@/lib/table-sort";

const DELIVERY_STATES: Record<Incoming["state"], string> = {
  expected: "Expected",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

const DELIVERY_SORTS: Record<string, (r: Incoming) => SortValue> = {
  ordered: (r) => ({ text: r.created_at }),
  product: (r) => ({ text: r.item_name }),
  unit: (r) => ({ text: r.item_unit }),
  quantity: (r) => ({ decimal: r.quantity }),
  received: (r) => ({ decimal: r.received }),
  expected: (r) => ({ text: r.expected_on }),
  reference: (r) => ({ text: r.reference }),
  unit_cost: (r) => ({ decimal: r.unit_cost }),
  state: (r) => ({ text: r.state }),
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
  const raw = await searchParams;
  const { created } = raw;
  const recordId = requireUuid(id);
  const [supplier, role, organization, history, deliveries] = await Promise.all(
    [
      serverRead<Supplier>(orgId, `/api/suppliers/${recordId}`),
      readActiveRole(orgId),
      serverRead<Organization>(orgId, "/api/organization"),
      readRecordHistory(orgId, "supplier", recordId),
      serverRead<Incoming[]>(
        orgId,
        "/api/inventory/incoming",
        `?${new URLSearchParams({ supplier_id: recordId, open_only: "false" })}`,
      ),
    ],
  );

  const deliverySort = tableSort(raw, `/o/${orgId}/suppliers/${recordId}`, Object.keys(DELIVERY_SORTS), "deliveries");

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
      <p className="-mt-3 text-sm text-zinc-500" data-testid="record-number">
        Supplier no. {supplier.number}
      </p>
      {created === "1" && <Notice testId="created">Supplier created.</Notice>}
      <RecordMeta
        record={supplier}
        people={history.history.people}
        timeZone={organization.timezone}
      />
      {canWriteRecords(role) ? (
        <SupplierForm key={supplier.id} supplier={supplier} />
      ) : (
        <SupplierDetails supplier={supplier} />
      )}
      <CollapsibleSection
        title="Deliveries from this supplier"
        count={deliveries.length}
        testId="supplier-deliveries-section"
        toggleTestId="deliveries-toggle"
      >
        {deliveries.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-deliveries">
            None yet. Choose this supplier when you record incoming stock on a
            product.
          </p>
        ) : (
          <table
            className="text-left text-sm"
            data-testid="supplier-deliveries"
          >
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <SortHeader label="Order date" sortKey="ordered" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.ordered} />
                <SortHeader label="Product" sortKey="product" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.product} />
                <SortHeader label="Unit" sortKey="unit" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.unit} />
                <SortHeader label="Ordered" sortKey="quantity" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.quantity} align="right" />
                <SortHeader label="Received" sortKey="received" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.received} align="right" />
                <SortHeader label="Expected" sortKey="expected" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.expected} />
                <SortHeader label="Reference" sortKey="reference" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.reference} />
                <SortHeader label="Unit cost excl. VAT" sortKey="unit_cost" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.unit_cost} align="right" />
                <SortHeader label="State" sortKey="state" current={deliverySort.sort} dir={deliverySort.dir} href={deliverySort.hrefs.state} last />
              </tr>
            </thead>
            <tbody>
              {sortRows(deliveries, deliverySort, DELIVERY_SORTS).map((row) => (
                <tr
                  key={row.id}
                  data-testid="supplier-delivery"
                  className="border-b border-zinc-200 dark:border-zinc-800"
                >
                  <td className="py-1 pr-4" data-testid="delivery-ordered-on">
                    {formatDay(row.created_at, organization.timezone)}
                  </td>
                  <td className="py-1 pr-4">
                    <Link
                      href={`/o/${orgId}/catalog/${row.item_id}`}
                      className="underline"
                    >
                      {row.item_name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{row.item_unit}</td>
                  <td className="py-1 pr-4 text-right">
                    {trimQuantity(row.quantity)}
                  </td>
                  <td className="py-1 pr-4 text-right">
                    {trimQuantity(row.received)}
                  </td>
                  <td className="py-1 pr-4">{row.expected_on ?? "—"}</td>
                  <td className="py-1 pr-4">{row.reference}</td>
                  <td
                    className="py-1 pr-4 text-right"
                    data-testid="delivery-unit-cost"
                  >
                    {row.unit_cost !== null && (
                      <DecimalText value={row.unit_cost} />
                    )}
                  </td>
                  <td className="py-1">{DELIVERY_STATES[row.state]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CollapsibleSection>
      <RecordHistory
        data={history}
        entityType="supplier"
        timeZone={organization.timezone}
      />
    </div>
  );
}
