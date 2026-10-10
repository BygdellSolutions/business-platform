import Link from "next/link";

import { DecimalText } from "@/components/ui/DecimalText";
import { ListFilters } from "@/components/ui/ListFilters";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { SortHeader } from "@/components/ui/SortHeader";
import { StockBadges } from "@/features/catalog/StockBadges";
import { ITEM_TYPES, ITEM_TYPE_LABELS } from "@/features/catalog/item-types";
import { readActiveRole } from "@/lib/active-role";
import type { Item, ItemAvailability } from "@/lib/api/types";
import {
  backendQuery,
  listHref, sortHref,
  pageOf,
  parseListParams,
} from "@/lib/list-params";
import { canWriteRecords } from "@/lib/roles";
import { serverRead } from "@/lib/server-api";
import { trimQuantity } from "@/lib/decimal";
import { formatShortDate } from "@/lib/dates";

const SORTS = ["number", "name", "sku", "type", "unit", "price", "promotion", "promotion_ends", "current_price", "price_inc_vat", "vat_rate", "on_hand", "allocated", "available", "committed", "incoming", "active"] as const;

const TYPES = ITEM_TYPES;

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
      <td className="py-1 pr-4 text-right">{trimQuantity(figures.incoming)}</td>
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
  const list = parseListParams(await searchParams, TYPES, [], {}, SORTS);
  const { rows: items, hasNext } = pageOf(
    await serverRead<Item[]>(orgId, "/api/items", backendQuery(list)),
  );
  const tracked = items.filter((item) => item.track_stock);
  const stock = new Map(
    tracked.length === 0
      ? []
      : (
          await serverRead<ItemAvailability[]>(
            orgId,
            "/api/inventory/availability",
            `?${new URLSearchParams(tracked.map((item) => ["item_id", item.id]))}`,
          )
        ).map((entry) => [entry.item_id, entry]),
  );
  const base = `/o/${orgId}/catalog`;
  const filtered = list.q !== "" || list.active !== "all" || list.type !== "";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Catalog</h1>
        {canWrite && (
          <Link
            href={`${base}/new`}
            className="underline"
            data-testid="new-item"
          >
            New item
          </Link>
        )}
      </div>

      <ListFilters action={base} params={list}>
        <label className="flex flex-col gap-1 text-sm">
          Type
          <select
            name="type"
            defaultValue={list.type}
            className="rounded border border-zinc-400 px-2 py-1 text-sm dark:bg-zinc-900"
          >
            <option value="">All</option>
            {ITEM_TYPES.map((type) => (
              <option key={type} value={type}>
                {ITEM_TYPE_LABELS[type]}
              </option>
            ))}
          </select>
        </label>
      </ListFilters>

      {items.length === 0 ? (
        <p data-testid="empty">
          {filtered ? "No items match." : "No items yet."}
        </p>
      ) : (
        // Wide on purpose (prices and stock side by side): the table scrolls sideways instead of squeezing cells.
        <div className="overflow-x-auto">
          <table
            data-testid="items-table"
            className="w-full text-left text-sm whitespace-nowrap"
          >
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <SortHeader label="No." sortKey="number" current={list.sort} dir={list.dir} href={sortHref(base, list, "number")} align="right" />
                <SortHeader label="Product" sortKey="name" current={list.sort} dir={list.dir} href={sortHref(base, list, "name")} />
                <SortHeader label="SKU" sortKey="sku" current={list.sort} dir={list.dir} href={sortHref(base, list, "sku")} />
                <SortHeader label="Type" sortKey="type" current={list.sort} dir={list.dir} href={sortHref(base, list, "type")} />
                <SortHeader label="Unit" sortKey="unit" current={list.sort} dir={list.dir} href={sortHref(base, list, "unit")} />
                <SortHeader label="Base price" sortKey="price" current={list.sort} dir={list.dir} href={sortHref(base, list, "price")} align="right" title="The normal price, excl. VAT (a promotion never changes it)" />
                <SortHeader label="Promotion" sortKey="promotion" current={list.sort} dir={list.dir} href={sortHref(base, list, "promotion")} />
                <SortHeader label="Promotion duration" sortKey="promotion_ends" current={list.sort} dir={list.dir} href={sortHref(base, list, "promotion_ends")} />
                <SortHeader label="Current price" sortKey="current_price" current={list.sort} dir={list.dir} href={sortHref(base, list, "current_price")} align="right" title="What is charged today, excl. VAT (customer discounts not included)" />
                <SortHeader label="VAT" sortKey="vat_rate" current={list.sort} dir={list.dir} href={sortHref(base, list, "vat_rate")} align="right" />
                <SortHeader label="Incl. VAT" sortKey="price_inc_vat" current={list.sort} dir={list.dir} href={sortHref(base, list, "price_inc_vat")} align="right" title="The current price incl. VAT: what a customer pays today" />
                <SortHeader label="On hand" sortKey="on_hand" current={list.sort} dir={list.dir} href={sortHref(base, list, "on_hand")} align="right" />
                <SortHeader label="Allocated" sortKey="allocated" current={list.sort} dir={list.dir} href={sortHref(base, list, "allocated")} align="right" />
                <SortHeader label="Available" sortKey="available" current={list.sort} dir={list.dir} href={sortHref(base, list, "available")} align="right" />
                <SortHeader label="Backordered" sortKey="committed" current={list.sort} dir={list.dir} href={sortHref(base, list, "committed")} align="right" />
                <SortHeader label="Incoming" sortKey="incoming" current={list.sort} dir={list.dir} href={sortHref(base, list, "incoming")} align="right" />
                <th className="py-1 pr-4">Stock</th>
                <SortHeader label="Status" sortKey="active" current={list.sort} dir={list.dir} href={sortHref(base, list, "active")} last />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr
                  key={item.id}
                  data-testid="item-row"
                  className="border-b border-zinc-200 dark:border-zinc-800"
                >
                  <td className="py-1 pr-4 text-right" data-testid="record-number">
                    {item.number}
                  </td>
                  <td className="py-1 pr-4">
                    <Link href={`${base}/${item.id}`} className="underline">
                      {item.name}
                    </Link>
                  </td>
                  <td className="py-1 pr-4" data-testid="item-sku">
                    {item.sku}
                  </td>
                  <td className="py-1 pr-4">{ITEM_TYPE_LABELS[item.type]}</td>
                  <td className="py-1 pr-4">{item.unit}</td>
                  {/* Every amount is exactly the string the backend sent: no parsing, rounding or arithmetic here. */}
                  <td className="py-1 pr-4 text-right" data-testid="item-price">
                    <DecimalText value={item.price_ex_vat} />
                  </td>
                  <td className="py-1 pr-4" data-testid="current-discount">
                    {item.current_discount ? (
                      <span className="text-red-700 dark:text-red-400">−{trimQuantity(item.current_discount.percent)}%</span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-1 pr-4" data-testid="promotion-duration">
                    {item.current_discount
                      ? `${formatShortDate(item.current_discount.starts_on)} – ${item.current_discount.ends_on ? formatShortDate(item.current_discount.ends_on) : "no end date"}`
                      : "—"}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="item-current-price">
                    {item.current_price_ex_vat !== null && <DecimalText value={item.current_price_ex_vat} />}
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="item-vat">
                    {trimQuantity(item.vat_rate)}%
                  </td>
                  <td className="py-1 pr-4 text-right" data-testid="item-price-inc-vat">
                    {item.current_price_inc_vat !== null && <DecimalText value={item.current_price_inc_vat} />}
                  </td>
                  <StockCells figures={stock.get(item.id)} />
                  <td className="py-1">
                    <StatusBadge active={item.active} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Pagination
        page={list.page}
        hasNext={hasNext}
        hrefFor={(page) => listHref(base, list, { page })}
      />
    </div>
  );
}
