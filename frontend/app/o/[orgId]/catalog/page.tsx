import Link from "next/link";

import { PRIMARY_LINK } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { FILTER_LABEL } from "@/components/ui/controls";
import { EmptyState } from "@/components/ui/EmptyState";
import { ListFilters } from "@/components/ui/ListFilters";
import { PageHeader } from "@/components/ui/PageHeader";
import { Pagination } from "@/components/ui/Pagination";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { SortHeader } from "@/components/ui/SortHeader";
import { ITEM_TYPES, ITEM_TYPE_LABELS } from "@/features/catalog/item-types";
import { readActiveRole } from "@/lib/active-role";
import type { Item } from "@/lib/api/types";
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

const SORTS = ["number", "name", "sku", "type", "price", "promotion", "promotion_ends", "current_price", "price_inc_vat", "vat_rate", "active"] as const;

const TYPES = ITEM_TYPES;

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
  const base = `/o/${orgId}/catalog`;
  const filtered = list.q !== "" || list.active !== "all" || list.type !== "";

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Catalog"
        description="The products and services you sell, with price and VAT."
        actions={
        canWrite && (
          <Link
            href={`${base}/new`}
            className={PRIMARY_LINK}
            data-testid="new-item"
          >
            New item
          </Link>
        )
        }
      />

      <div className="panel flex flex-col gap-4">
        <ListFilters action={base} params={list}>
          <label className={FILTER_LABEL}>
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
          <EmptyState
            title={filtered ? "No items match." : "No items yet."}
            hint={filtered ? "Try a different search or clear the filters." : "Items are the products and services you sell, with price and VAT."}
            action={!filtered && canWrite ? <Link href={`${base}/new`} className={PRIMARY_LINK}>Add your first item</Link> : undefined}
          />
        ) : (
          // Wide on purpose (many price columns side by side): the table scrolls sideways instead of squeezing cells.
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
                  <SortHeader label="Base price" sortKey="price" current={list.sort} dir={list.dir} href={sortHref(base, list, "price")} align="right" title="The normal price, excl. VAT (a promotion never changes it)" />
                  <SortHeader label="Promotion" sortKey="promotion" current={list.sort} dir={list.dir} href={sortHref(base, list, "promotion")} />
                  <SortHeader label="Promotion duration" sortKey="promotion_ends" current={list.sort} dir={list.dir} href={sortHref(base, list, "promotion_ends")} />
                  <SortHeader label="Current price" sortKey="current_price" current={list.sort} dir={list.dir} href={sortHref(base, list, "current_price")} align="right" title="What is charged today, excl. VAT (customer discounts not included)" />
                  <SortHeader label="VAT" sortKey="vat_rate" current={list.sort} dir={list.dir} href={sortHref(base, list, "vat_rate")} align="right" />
                  <SortHeader label="Incl. VAT" sortKey="price_inc_vat" current={list.sort} dir={list.dir} href={sortHref(base, list, "price_inc_vat")} align="right" title="The current price incl. VAT: what a customer pays today" />
                  <th className="py-1 pr-4">Tracks stock</th>
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
                    <td className="py-1 pr-4" data-testid="item-tracks-stock">
                      {item.track_stock ? "Yes" : "No"}
                    </td>
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
    </div>
  );
}
