import { DecimalText } from "@/components/ui/DecimalText";
import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Item } from "@/lib/api/types";

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
        {
          label: "Price excl. VAT",
          value: <DecimalText value={item.price_ex_vat} />,
          testId: "detail-price",
        },
        {
          label: "Price incl. VAT",
          value: <DecimalText value={item.price_inc_vat} />,
          testId: "detail-price-inc-vat",
        },
        { label: "VAT %", value: <DecimalText value={item.vat_rate} /> },
        {
          label: "Discount today",
          value: item.current_discount ? (
            <>
              −<DecimalText value={item.current_discount.percent} /> %{" "}
              {item.current_discount.ends_on
                ? `until ${item.current_discount.ends_on}`
                : "(no end date)"}
            </>
          ) : null,
          testId: "detail-current-discount",
        },
        ...(item.promotion_price_ex_vat !== null &&
        item.promotion_price_inc_vat !== null
          ? [
              {
                label: "Promotion price",
                value: (
                  <>
                    <DecimalText value={item.promotion_price_ex_vat} /> excl.
                    VAT, <DecimalText value={item.promotion_price_inc_vat} />{" "}
                    incl. VAT
                  </>
                ),
                testId: "detail-promotion-price",
              },
            ]
          : []),
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
