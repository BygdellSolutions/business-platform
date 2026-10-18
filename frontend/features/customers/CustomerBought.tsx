"use client";

import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import { OrderCells, OrderHeaders } from "@/components/history/OrderColumns";
import type { BoughtLine } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: BoughtLine) => SortValue> = {
  number: (r) => ({ number: r.transaction_number }),
  date: (r) => ({ text: r.transaction_date }),
  status: (r) => ({ text: r.status }),
  description: (r) => ({ text: r.description }),
  quantity: (r) => ({ decimal: r.quantity }),
  unit_price: (r) => ({ decimal: r.unit_price_ex_vat }),
  amount: (r) => ({ decimal: r.gross_amount }),
};

/**
 * What a customer bought besides services (catalog items and ad-hoc lines), newest order first, with a link to the
 * product and the order. Quantity and unit are separate columns; amounts are the backend's strings.
 */
export function CustomerBought({ orgId, lines }: { orgId: string; lines: BoughtLine[] }) {
  const sorted = useSortedRows(lines, SORT_COLUMNS);
  return (
    <CollapsibleSection title="Products and other lines bought" count={lines.length} testId="customer-bought" toggleTestId="bought-toggle">
      {lines.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-bought">
          Nothing yet (services are listed separately).
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <OrderHeaders header={sorted.header} />
                <SortHeader label="Description" {...sorted.header("description")} />
                <SortHeader label="Qty" {...sorted.header("quantity")} align="right" />
                <SortHeader label="Unit price" {...sorted.header("unit_price")} align="right" />
                <SortHeader label="Amount" {...sorted.header("amount")} align="right" last />
              </tr>
            </thead>
            <tbody>
              {sorted.rows.map((line) => (
                <tr key={line.line_id} data-testid="bought-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <OrderCells orgId={orgId} transactionId={line.transaction_id} number={line.transaction_number} date={line.transaction_date} status={line.status} />
                  <td className="py-1 pr-4">
                    {line.item_id ? (
                      <Link href={`/o/${orgId}/catalog/${line.item_id}`} className="underline">
                        {line.description}
                      </Link>
                    ) : (
                      line.description
                    )}
                  </td>
                  <td className="py-1 pr-4 text-right">{trimQuantity(line.quantity)}</td>
                  <td className="py-1 pr-4 text-right">
                    <DecimalText value={line.unit_price_ex_vat} />
                  </td>
                  <td className="py-1 text-right">
                    <DecimalText value={line.gross_amount} /> {line.currency ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CollapsibleSection>
  );
}
