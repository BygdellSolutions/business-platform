"use client";

import { DecimalText } from "@/components/ui/DecimalText";
import { useEditor } from "@/features/transactions/editor-context";

/**
 * Net, VAT, gross and the VAT breakdown, exactly as FastAPI calculated them (sums of the stored
 * line amounts). This component adds nothing up and rounds nothing: it prints strings. While
 * the page is re-reading the transaction the numbers are marked as being updated, so a total
 * that is about to change is never presented as current.
 */
export function TotalsPanel() {
  const { transaction, refreshing } = useEditor();
  const { totals } = transaction;

  return (
    <section aria-label="Totals" aria-busy={refreshing} data-testid="totals" data-updating={refreshing || undefined} className={`flex flex-col gap-2 ${refreshing ? "opacity-50" : ""}`}>
      <h2 className="text-lg font-medium">
        Totals {refreshing && <span className="text-sm font-normal text-zinc-500">(updating…)</span>}
      </h2>
      <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
        <dt>Currency</dt>
        <dd className="text-right" data-testid="total-currency">
          {/* Never guessed: a transaction that predates currencies says so. */}
          {transaction.currency ?? <span className="text-zinc-500">No currency recorded</span>}
        </dd>
        <dt>Net (excl. VAT)</dt>
        <dd className="text-right" data-testid="total-net">
          <DecimalText value={totals.net_amount} />
        </dd>
        <dt>VAT</dt>
        <dd className="text-right" data-testid="total-vat">
          <DecimalText value={totals.vat_amount} />
        </dd>
        <dt className="font-medium">Gross (incl. VAT)</dt>
        <dd className="text-right font-medium" data-testid="total-gross">
          <DecimalText value={totals.gross_amount} />
        </dd>
      </dl>

      {totals.vat_breakdown.length > 0 && (
        <table data-testid="vat-breakdown" className="max-w-md text-left text-sm">
          <caption className="pb-1 text-left text-zinc-500">VAT breakdown</caption>
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">VAT rate (%)</th>
              <th className="py-1 pr-4 text-right">Net</th>
              <th className="py-1 text-right">VAT</th>
            </tr>
          </thead>
          <tbody>
            {totals.vat_breakdown.map((row) => (
              <tr key={row.vat_rate} data-testid="vat-row">
                <td className="py-1 pr-4">
                  <DecimalText value={row.vat_rate} />
                </td>
                <td className="py-1 pr-4 text-right">
                  <DecimalText value={row.net_amount} />
                </td>
                <td className="py-1 text-right">
                  <DecimalText value={row.vat_amount} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
