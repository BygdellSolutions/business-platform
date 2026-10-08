import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { StockBadges } from "@/features/catalog/StockBadges";
import { readActiveRole } from "@/lib/active-role";
import type { Item, ItemAvailability } from "@/lib/api/types";
import { backendQuery, listHref, pageOf, parseListParams } from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";
import { trimQuantity } from "@/lib/decimal";

const TYPES = ["service", "product"] as const;

/** The stock columns of one row: empty for an item that does not track stock. */
function StockCells({ figures }: { figures: ItemAvailability | undefined }) {
  if (!figures) return <td colSpan={6} />;
  return (
    <>
      <td className="py-1 pr-4 text-right" data-testid="item-on-hand">
        {trimQuantity(figures.on_hand)}
      </td>
      <td className="py-1 pr-4 text-right" data-testid="item-allocated">
        {trimQuantity(figures.allocated)}
      </td>
      <td className="py-1 pr-4 text-right" data-testid="item-available">
        {trimQuantity(figures.available)}
      </td>
      <td className="py-1 pr-4 text-right">
        {trimQuantity(figures.committed)}
      </td>
      <td className="py-1 pr-4 text-right">
        {trimQuantity(figures.incoming)}
      </td>
      <td className="py-1 pr-4" data-testid="item-stock-states">
        <StockBadges states={figures.states} />
      </td>
    </>
  );
}

export default async function CatalogPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgId } = await params;
  const canWrite = canWriteRecords(await readActiveRole(orgId));
  const list = parseListParams(await searchParams, TYPES);
  const { rows: items, hasNext } = pageOf(await serverRead<Item[]>(orgId, "/api/items", backendQuery(list)));
  const tracked = items.filter((item) => item.track_stock);
  const stock = new Map(
    tracked.length === 0
      ? []
      : (
          await serverRead<ItemAvailability[]>(orgId, "/api/inventory/availability", `?${new URLSearchParams(tracked.map((item) => ["item_id", item.id]))}`)
        ).map((entry) => [entry.item_id, entry]),
  );
  const base = `/o/${orgId}/catalog`;
  const filtered = list.q !== "" || list.active !== "all" || list.type !== "";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Catalog</h1>
        {canWrite && (
          <Link href={`${base}/new`} className="underline" data-testid="new-item">
            New item
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list}>
        <label className="flex flex-col gap-1 text-sm">
          Type
          <select name="type" defaultValue={list.type} className="rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900">
            <option value="">All</option>
            <option value="service">Service</option>
            <option value="product">Product</option>
          </select>
        </label>
      </ListFilters>

      {items.length === 0 ? (
        <p data-testid="empty">{filtered ? "No items match." : "No items yet."}</p>
      ) : (
        <table data-testid="items-table" className="w-full max-w-6xl text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Name</th>
              <th className="py-1 pr-4">SKU</th>
              <th className="py-1 pr-4">Type</th>
              <th className="py-1 pr-4">Unit</th>
              <th className="py-1 pr-4 text-right">Price excl. VAT</th>
              <th className="py-1 pr-4 text-right">VAT %</th>
              <th className="py-1 pr-4 text-right">On hand</th>
              <th className="py-1 pr-4 text-right">Allocated</th>
              <th className="py-1 pr-4 text-right">Available</th>
              <th className="py-1 pr-4 text-right">Backordered</th>
              <th className="py-1 pr-4 text-right">Incoming</th>
              <th className="py-1 pr-4">Stock</th>
              <th className="py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} data-testid="item-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4">
                  <Link href={`${base}/${item.id}`} className="underline">
                    {item.name}
                  </Link>
                </td>
                <td className="py-1 pr-4" data-testid="item-sku">{item.sku}</td>
                <td className="py-1 pr-4">{item.type}</td>
                <td className="py-1 pr-4">{item.unit}</td>
                <td className="py-1 pr-4 text-right" data-testid="item-price">
                  {/* Exactly the string the backend sent: no parsing, rounding or formatting. */}
                  <DecimalText value={item.price_ex_vat} />
                  {item.current_discount && (
                    <span className="ml-1 text-xs text-red-700 dark:text-red-400" data-testid="current-discount">
                      −<DecimalText value={item.current_discount.percent} />% now
                    </span>
                  )}
                </td>
                <td className="py-1 pr-4 text-right" data-testid="item-vat">
                  <DecimalText value={item.vat_rate} />
                </td>
                <StockCells figures={stock.get(item.id)} />
                <td className="py-1">
                  <StatusBadge active={item.active} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Pagination page={list.page} hasNext={hasNext} hrefFor={(page) => listHref(base, list, { page })} />
    </div>
  );
}
