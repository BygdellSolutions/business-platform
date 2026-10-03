"use client";

import { LineRow } from "@/features/transactions/LineRow";
import { useEditor } from "@/features/transactions/editor-context";

/**
 * The lines, in the order the server sent them (no sorting, no reordering). The row number is
 * only a label for the position in this list; positions on the server can have gaps after a
 * delete. Rows are keyed by the line's id, so a row keeps its identity across refreshes.
 */
export function LinesTable() {
  const { transaction, refreshing } = useEditor();

  return (
    <section aria-label="Lines" aria-busy={refreshing} data-testid="lines" data-updating={refreshing || undefined} className={`flex flex-col gap-2 ${refreshing ? "opacity-50" : ""}`}>
      <h2 className="text-lg font-medium">Lines</h2>
      {transaction.lines.length === 0 ? (
        <p data-testid="no-lines">No lines yet.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-3">#</th>
                <th className="py-1 pr-3">Description</th>
                <th className="py-1 pr-3">Unit</th>
                <th className="py-1 pr-3 text-right">Quantity</th>
                <th className="py-1 pr-3 text-right">Unit price excl. VAT</th>
                <th className="py-1 pr-3 text-right">VAT %</th>
                <th className="py-1 pr-3 text-right">Net</th>
                <th className="py-1 pr-3 text-right">VAT</th>
                <th className="py-1 pr-3 text-right">Gross</th>
                <th className="py-1 pr-3">Item</th>
                <th className="py-1">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {transaction.lines.map((line, index) => (
                <LineRow key={line.id} line={line} ordinal={index + 1} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
