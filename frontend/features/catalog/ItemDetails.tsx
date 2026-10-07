import { DecimalText } from "@/components/ui/DecimalText";
import { DetailList } from "@/components/ui/DetailList";
import { StatusBadge } from "@/components/ui/StatusBadge";
import type { Item } from "@/lib/api/types";

const TYPE_LABELS = { service: "Service", product: "Product" } as const;

/** A catalog item for someone who may read it but not change it. Amounts are the backend's strings. */
export function ItemDetails({ item }: { item: Item }) {
  return (
    <DetailList
      testId="record-details"
      details={[
        { label: "Type", value: TYPE_LABELS[item.type] },
        { label: "Name", value: item.name },
        { label: "Description", value: item.description },
        { label: "Unit", value: item.unit },
        { label: "Price excl. VAT", value: <DecimalText value={item.price_ex_vat} />, testId: "detail-price" },
        { label: "VAT %", value: <DecimalText value={item.vat_rate} /> },
        { label: "Status", value: <StatusBadge active={item.active} /> },
      ]}
    />
  );
}
