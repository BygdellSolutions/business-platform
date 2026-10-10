import Link from "next/link";

import { BACKORDER_STATES } from "@/features/catalog/backorder-labels";
import { StockBadges } from "@/features/catalog/StockBadges";
import { SupplierName } from "@/features/suppliers/SupplierName";
import type { Backorder, Incoming, StockItem } from "@/lib/api/types";
import { serverRead } from "@/lib/server-api";
import { trimQuantity } from "@/lib/decimal";

const STATES = [
  { value: "", label: "All" },
  { value: "in_stock", label: "In stock" },
  { value: "low_stock", label: "Low stock" },
  { value: "out_of_stock", label: "Out of stock" },
  { value: "backordered", label: "Backordered" },
  { value: "incoming", label: "Incoming" },
] as const;
const CONTROL = "rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900";

function one(value: string | string[] | undefined): string {
  return typeof value === "string" ? value : "";
}

/**
 * The organization's stock at a glance: every product that tracks stock with what is on hand, promised, available and
 * on its way; then the open backorders (oldest first) and the deliveries on their way. Counting, receiving and
 * allocating happen on each product's page, where the stock they change is shown.
 */
export default async function InventoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const raw = await searchParams;
  const q = one(raw.q).trim();
  const state = STATES.some((entry) => entry.value === one(raw.state)) ? one(raw.state) : "";
  const query = new URLSearchParams({ ...(q ? { q } : {}), ...(state ? { state } : {}) }).toString();
  const [items, backorders, incoming] = await Promise.all([
    serverRead<StockItem[]>(orgId, "/api/inventory/items", query ? `?${query}` : ""),
    serverRead<Backorder[]>(orgId, "/api/inventory/backorders"),
    serverRead<Incoming[]>(orgId, "/api/inventory/incoming"),
  ]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Inventory</h1>
      <section aria-label="Stock" className="flex flex-col gap-2">
        <h2 className="text-lg font-semibold">Stock</h2>
        <p className="text-xs text-zinc-500">
          Allocated: on open draft sales (not final; whichever sale is completed first gets the stock). Committed: completed sales still waiting for
          stock. Available = on hand − allocated − committed.
        </p>
        <form action={`/o/${orgId}/inventory`} className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Search
            <input name="q" defaultValue={q} placeholder="Name or SKU" className={CONTROL} />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            State
            <select name="state" defaultValue={state} className={CONTROL}>
              {STATES.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className="rounded border border-zinc-400 px-3 py-1 text-sm">
            Show
          </button>
        </form>
        {items.length === 0 ? (
          <p className="text-sm text-zinc-500" data-testid="no-stock-items">
            {q || state ? "No product matches." : "No product tracks stock yet. Open a product in the Catalog and tick “Track stock”."}
          </p>
        ) : (
          <table className="w-full max-w-6xl text-left text-sm" data-testid="stock-table">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Product</th>
                <th className="py-1 pr-4">SKU</th>
                <th className="py-1 pr-4">Unit</th>
                <th className="py-1 pr-4 text-right">On hand</th>
                <th className="py-1 pr-4 text-right" title="On open draft sales: meant for a customer, not final yet">
                  Allocated
                </th>
                <th className="py-1 pr-4 text-right" title="Completed sales still waiting for stock (backorders)">
                  Committed
                </th>
                <th className="py-1 pr-4 text-right">Available</th>
                <th className="py-1 pr-4 text-right">Incoming</th>
                <th className="py-1 pr-4 text-right">Low below</th>
                <th className="py-1">State</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.item_id} data-testid="stock-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">
                    <Link href={`/o/${orgId}/catalog/${item.item_id}`} className="underline">
                      {item.name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4">{item.sku}</td>
                  <td className="py-1 pr-4">{item.unit}</td>
                  <td className="py-1 pr-4 text-right" data-testid="stock-row-on-hand">
                    {trimQuantity(item.on_hand)}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="stock-row-allocated">
                    {trimQuantity(item.allocated)}
                  </td>
                  <td className="py-1 pr-4 text-right">
                    {trimQuantity(item.committed)}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="stock-row-available">
                    {trimQuantity(item.available)}
                  </td>
                  <td className="py-1 pr-4 text-right">
                    {trimQuantity(item.incoming)}
                  </td>
                  <td className="py-1 pr-4 text-right">{item.low_stock_threshold ? trimQuantity(item.low_stock_threshold) : null}</td>
                  <td className="py-1">
                    <StockBadges states={item.states} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
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
                <th className="py-1 pr-4">Unit</th>
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
                  <td className="py-1 pr-4">{backorder.item_unit}</td>
                  <td className="py-1 pr-4 text-right" data-testid="backlog-waiting">
                    {trimQuantity(backorder.remaining)}
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
                <th className="py-1 pr-4">Unit</th>
                <th className="py-1 pr-4 text-right">Ordered</th>
                <th className="py-1 pr-4 text-right">Received</th>
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
                  <td className="py-1 pr-4">{row.item_unit}</td>
                  <td className="py-1 pr-4 text-right" data-testid="incoming-overview-ordered">
                    {trimQuantity(row.quantity)}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="incoming-overview-received">
                    {trimQuantity(row.received)}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="incoming-overview-remaining">
                    {trimQuantity(row.remaining)}
                  </td>
                  <td className="py-1 pr-4">
                    <SupplierName orgId={orgId} supplier={row.supplier} />
                  </td>
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
