import { DecimalText } from "@/components/ui/DecimalText";
import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Item } from "@/lib/api/types";
import { formatShortDate } from "@/lib/dates";
import { trimQuantity } from "@/lib/decimal";

import { ITEM_TYPE_LABELS } from "./item-types";

/** A catalog item for someone who may read it but not change it. Amounts are the backend's strings. */
export function ItemDetails({ item }: { item: Item }) {
  return (
    <DetailList
      testId="record-details"
      details={[
        { label: "Type", value: ITEM_TYPE_LABELS[item.type] },
        { label: "Article number", value: item.sku },
        { label: "Name", value: item.name },
        { label: "Description", value: item.description },
        { label: "Unit", value: item.unit },
        // The same order and meaning as the catalog's price columns: every price excl. VAT except "Incl. VAT".
        { label: "Base price", value: <DecimalText value={item.price_ex_vat} />, testId: "detail-price" },
        {
          label: "Promotion",
          value: item.current_discount ? `−${trimQuantity(item.current_discount.percent)}%` : "—",
          testId: "detail-current-discount",
        },
        {
          label: "Promotion duration",
          value: item.current_discount
            ? `${formatShortDate(item.current_discount.starts_on)} – ${item.current_discount.ends_on ? formatShortDate(item.current_discount.ends_on) : "no end date"}`
            : "—",
          testId: "detail-promotion-duration",
        },
        {
          label: "Current price",
          value: item.current_price_ex_vat !== null ? <DecimalText value={item.current_price_ex_vat} /> : null,
          testId: "detail-current-price",
        },
        { label: "VAT", value: `${trimQuantity(item.vat_rate)}%` },
        {
          label: "Incl. VAT",
          value: item.current_price_inc_vat !== null ? <DecimalText value={item.current_price_inc_vat} /> : null,
          testId: "detail-price-inc-vat",
        },
        ...(item.type === "product"
          ? [
              {
                label: "Stock",
                value: item.track_stock ? "Tracked" : "Not tracked",
              },
            ]
          : []),
        ...(item.track_stock
          ? [{ label: "Low-stock threshold", value: item.low_stock_threshold }]
          : []),
        { label: "Status", value: <StatusBadge active={item.active} /> },
      ]}
    />
  );
}
