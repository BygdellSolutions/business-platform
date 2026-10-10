import Link from "next/link";

import { BACKORDER_STATES } from "@/features/catalog/backorder-labels";
import { StockBadges } from "@/features/catalog/StockBadges";
import { SupplierName } from "@/features/suppliers/SupplierName";
import type { Backorder, Incoming, StockItem } from "@/lib/api/types";
import { serverRead } from "@/lib/server-api";
import { trimQuantity } from "@/lib/decimal";
import { SortHeader } from "@/components/ui/SortHeader";
import { sortRows, tableSort, type SortValue } from "@/lib/table-sort";

const STATES = [
  { value: "", label: "All" },
  { value: "in_stock", label: "In stock" },
  { value: "low_stock", label: "Low stock" },
  { value: "out_of_stock", label: "Out of stock" },
  { value: "backordered", label: "Backordered" },
  { value: "incoming", label: "Incoming" },
] as const;
const PRODUCT_SORTS = ["name", "sku", "unit", "on_hand", "allocated", "committed", "available", "incoming", "low_stock_threshold"];
const BACKLOG_SORTS: Record<string, (r: Backorder) => SortValue> = {
  number: (r) => ({ number: r.transaction_number }),
  date: (r) => ({ text: r.transaction_date }),
  customer: (r) => ({ text: r.customer_name }),
  product: (r) => ({ text: r.item_name }),
  unit: (r) => ({ text: r.item_unit }),
  waiting: (r) => ({ decimal: r.remaining }),
  state: (r) => ({ text: r.state }),
};
const INCOMING_SORTS: Record<string, (r: Incoming) => SortValue> = {
  expected: (r) => ({ text: r.expected_on }),
  product: (r) => ({ text: r.item_name }),
  unit: (r) => ({ text: r.item_unit }),
  quantity: (r) => ({ decimal: r.quantity }),
  received: (r) => ({ decimal: r.received }),
  remaining: (r) => ({ decimal: r.remaining }),
  supplier: (r) => ({ text: r.supplier?.name ?? null }),
  reference: (r) => ({ text: r.reference }),
};
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
  const base = `/o/${orgId}/inventory`;
  // Products are sorted by the backend (their figures are computed there); the two smaller tables on the page.
  const productSort = tableSort(raw, base, PRODUCT_SORTS, "sort");
  const backlogSort = tableSort(raw, base, Object.keys(BACKLOG_SORTS), "backlog");
  const incomingSort = tableSort(raw, base, Object.keys(INCOMING_SORTS), "incoming");
  const query = new URLSearchParams({
    ...(q ? { q } : {}),
    ...(state ? { state } : {}),
    ...(productSort.sort ? { sort: productSort.sort, dir: productSort.dir } : {}),
  }).toString();
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
          {productSort.sort && <input type="hidden" name="sort" value={productSort.sort} />}
          {productSort.sort && productSort.dir === "desc" && <input type="hidden" name="sort_dir" value="desc" />}
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
                <SortHeader label="Product" sortKey="name" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.name} />
                <SortHeader label="SKU" sortKey="sku" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.sku} />
                <SortHeader label="Unit" sortKey="unit" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.unit} />
                <SortHeader label="On hand" sortKey="on_hand" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.on_hand} align="right" />
                <SortHeader label="Allocated" sortKey="allocated" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.allocated} align="right" title="On open draft sales: meant for a customer, not final yet" />
                <SortHeader label="Committed" sortKey="committed" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.committed} align="right" title="Completed sales still waiting for stock (backorders)" />
                <SortHeader label="Available" sortKey="available" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.available} align="right" />
                <SortHeader label="Incoming" sortKey="incoming" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.incoming} align="right" />
                <SortHeader label="Low below" sortKey="low_stock_threshold" current={productSort.sort} dir={productSort.dir} href={productSort.hrefs.low_stock_threshold} align="right" />
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
                <SortHeader label="Order no." sortKey="number" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.number} align="right" />
                <SortHeader label="Completed" sortKey="date" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.date} />
                <SortHeader label="Customer" sortKey="customer" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.customer} />
                <SortHeader label="Product" sortKey="product" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.product} />
                <SortHeader label="Unit" sortKey="unit" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.unit} />
                <SortHeader label="Waiting" sortKey="waiting" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.waiting} align="right" />
                <SortHeader label="State" sortKey="state" current={backlogSort.sort} dir={backlogSort.dir} href={backlogSort.hrefs.state} />
              </tr>
            </thead>
            <tbody>
              {sortRows(backorders, backlogSort, BACKLOG_SORTS).map((backorder) => (
                <tr key={backorder.fulfillment_id} data-testid="backlog-row" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4 text-right" data-testid="order-number">
                    <Link href={`/o/${orgId}/transactions/${backorder.transaction_id}`} className="underline">
                      {backorder.transaction_number}
                    </Link>
                  </td>
                  <td className="py-1 pr-4" data-testid="order-date">
                    {backorder.transaction_date}
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
                <SortHeader label="Expected" sortKey="expected" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.expected} />
                <SortHeader label="Product" sortKey="product" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.product} />
                <SortHeader label="Unit" sortKey="unit" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.unit} />
                <SortHeader label="Ordered" sortKey="quantity" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.quantity} align="right" />
                <SortHeader label="Received" sortKey="received" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.received} align="right" />
                <SortHeader label="Still expected" sortKey="remaining" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.remaining} align="right" />
                <SortHeader label="Supplier" sortKey="supplier" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.supplier} />
                <SortHeader label="Reference" sortKey="reference" current={incomingSort.sort} dir={incomingSort.dir} href={incomingSort.hrefs.reference} />
              </tr>
            </thead>
            <tbody>
              {sortRows(incoming, incomingSort, INCOMING_SORTS).map((row) => (
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
