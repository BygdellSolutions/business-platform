import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { CollapsibleSection } from "@/components/ui/CollapsibleSection";
import type { BoughtLine } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";

/**
 * What a customer bought besides services (catalog items and ad-hoc lines), newest order first, with a link to the
 * product and the order. Quantity and unit are separate columns; amounts are the backend's strings.
 */
export function CustomerBought({ orgId, lines }: { orgId: string; lines: BoughtLine[] }) {
  return (
    <CollapsibleSection title="Products and other lines bought" count={lines.length} testId="customer-bought" toggleTestId="bought-toggle">
      {lines.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-bought">
          Nothing yet (services are listed separately).
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Order</th>
              <th className="py-1 pr-4">Description</th>
              <th className="py-1 pr-4 text-right">Qty</th>
              <th className="py-1 pr-4">Unit</th>
              <th className="py-1 pr-4 text-right">Unit price</th>
              <th className="py-1 text-right">Amount</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.line_id} data-testid="bought-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4">
                  <Link href={`/o/${orgId}/transactions/${line.transaction_id}`} className="underline">
                    Order {line.transaction_number} · {line.transaction_date}
                  </Link>{" "}
                  <span className="text-xs text-zinc-500">{line.status}</span>
                </td>
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
                <td className="py-1 pr-4">{line.unit}</td>
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
      )}
    </CollapsibleSection>
  );
}
