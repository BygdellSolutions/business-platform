"use client";

import Link from "next/link";
import { useState } from "react";

import { Button, ButtonIcon, ICONS } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { Modal } from "@/components/ui/Modal";
import { SortHeader } from "@/components/ui/SortHeader";
import type { Incoming } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";
import type { SortValue } from "@/lib/table-sort";
import { formatDay } from "@/lib/timestamps";
import { useSortedRows } from "@/lib/use-sorted-rows";

const DELIVERY_STATES: Record<Incoming["state"], string> = {
  expected: "Expected",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: Incoming) => SortValue> = {
  ordered: (r) => ({ text: r.created_at }),
  product: (r) => ({ text: r.item_name }),
  quantity: (r) => ({ decimal: r.quantity }),
  received: (r) => ({ decimal: r.received }),
  expected: (r) => ({ text: r.expected_on }),
  reference: (r) => ({ text: r.reference }),
  unit_cost: (r) => ({ decimal: r.unit_cost }),
  state: (r) => ({ text: r.state }),
};

/**
 * What belongs to a supplier besides its own information: a tinted button (beside "Deactivate supplier") that opens a pop-up window with
 * the deliveries from this supplier, as on invoices, catalog items, horses and customers.
 */
export function SupplierDialogs({ orgId, deliveries, timeZone }: { orgId: string; deliveries: Incoming[]; timeZone: string | null }) {
  const [open, setOpen] = useState(false);
  const sorted = useSortedRows(deliveries, SORT_COLUMNS);
  return (
    <>
      <Button type="button" variant="tonal" onClick={() => setOpen(true)} data-testid="open-deliveries">
        <ButtonIcon path={ICONS.incoming} />
        {`Deliveries (${deliveries.length})`}
      </Button>
      <Modal open={open} onClose={() => setOpen(false)} title="Deliveries from this supplier" testId="deliveries-dialog" wide>
        <div data-testid="supplier-deliveries-section" className="flex flex-col gap-2">
          {deliveries.length === 0 ? (
            <p className="text-sm text-zinc-500" data-testid="no-deliveries">
              None yet. Choose this supplier when you record incoming stock on a product.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="text-left text-sm" data-testid="supplier-deliveries">
                <thead>
                  <tr className="border-b border-zinc-300 dark:border-zinc-700">
                    <SortHeader label="Order date" {...sorted.header("ordered")} />
                    <SortHeader label="Product" {...sorted.header("product")} />
                    <SortHeader label="Ordered" {...sorted.header("quantity")} align="right" />
                    <SortHeader label="Received" {...sorted.header("received")} align="right" />
                    <SortHeader label="Expected" {...sorted.header("expected")} />
                    <SortHeader label="Reference" {...sorted.header("reference")} />
                    <SortHeader label="Unit cost excl. VAT" {...sorted.header("unit_cost")} align="right" />
                    <SortHeader label="State" {...sorted.header("state")} last />
                  </tr>
                </thead>
                <tbody>
                  {sorted.rows.map((row) => (
                    <tr key={row.id} data-testid="supplier-delivery" className="border-b border-zinc-200 dark:border-zinc-800">
                      <td className="py-1 pr-4" data-testid="delivery-ordered-on">
                        {formatDay(row.created_at, timeZone)}
                      </td>
                      <td className="py-1 pr-4">
                        <Link href={`/o/${orgId}/catalog/${row.item_id}`} className="underline">
                          {row.item_name}
                        </Link>
                      </td>
                      <td className="py-1 pr-4 text-right">{trimQuantity(row.quantity)}</td>
                      <td className="py-1 pr-4 text-right">{trimQuantity(row.received)}</td>
                      <td className="py-1 pr-4">{row.expected_on ?? "—"}</td>
                      <td className="py-1 pr-4">{row.reference}</td>
                      <td className="py-1 pr-4 text-right" data-testid="delivery-unit-cost">
                        {row.unit_cost !== null && <DecimalText value={row.unit_cost} />}
                      </td>
                      <td className="py-1">{DELIVERY_STATES[row.state]}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Modal>
    </>
  );
}
