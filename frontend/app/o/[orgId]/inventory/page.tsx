import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { BACKORDER_STATES } from "@/features/catalog/BackordersPanel";
import type { Backorder, Incoming } from "@/lib/api/types";
import { serverRead } from "@/lib/server-api";

/**
 * The organization's open backorders (oldest first) and the deliveries on their way, across all products. Allocation
 * and receipt happen on each product's page, where the stock they change is shown.
 */
export default async function InventoryPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  const [backorders, incoming] = await Promise.all([
    serverRead<Backorder[]>(orgId, "/api/inventory/backorders"),
    serverRead<Incoming[]>(orgId, "/api/inventory/incoming"),
  ]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Inventory</h1>
      <section aria-label="Backorders" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Backorders</h2>
        {backorders.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-backorders">
            No sale is waiting for stock.
          </p>
        ) : (
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Completed</th>
                <th className="py-1 pr-4">Customer</th>
                <th className="py-1 pr-4">Product</th>
                <th className="py-1 pr-4 text-right">Waiting</th>
                <th className="py-1 pr-4">State</th>
              </tr>
            </thead>
            <tbody>
              {backorders.map((backorder) => (
                <tr key={backorder.fulfillment_id} data-testid="backlog-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/transactions/${backorder.transaction_id}`} className="underline">
                      {backorder.transaction_date}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{backorder.customer_name}</td>
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/catalog/${backorder.item_id}`} className="underline">
                      {backorder.item_name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4 text-right">
                    <DecimalText value={backorder.remaining} /> {backorder.item_unit}
                  </td>
                  <td className="py-1 pr-4">{BACKORDER_STATES[backorder.state]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section aria-label="Incoming" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Incoming</h2>
        {incoming.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-incoming-overview">
            Nothing on its way.
          </p>
        ) : (
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Expected</th>
                <th className="py-1 pr-4">Product</th>
                <th className="py-1 pr-4 text-right">Still expected</th>
                <th className="py-1 pr-4">Supplier</th>
                <th className="py-1 pr-4">Reference</th>
              </tr>
            </thead>
            <tbody>
              {incoming.map((row) => (
                <tr key={row.id} data-testid="incoming-overview-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">{row.expected_on ?? <span className="text-zinc-500">not given</span>}</td>
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/catalog/${row.item_id}`} className="underline">
                      {row.item_name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4 text-right">
                    <DecimalText value={row.remaining} /> {row.item_unit}
                  </td>
                  <td className="py-1 pr-4">{row.supplier}</td>
                  <td className="py-1 pr-4">{row.reference}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
