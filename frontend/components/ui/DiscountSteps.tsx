import { DecimalText } from "@/components/ui/DecimalText";
import type { MoneyString, PercentString } from "@/lib/decimal";

/**
 * The discount layers of a line as stored: the list price, then the catalog discount, then the customer discount.
 * Each value is the backend's string; nothing is calculated here. Nothing is shown for a line without layers (ad-hoc
 * lines and typed prices).
 */
export function DiscountSteps({
  list,
  catalog,
  customer,
}: {
  list: MoneyString | null;
  catalog: PercentString | null;
  customer: PercentString | null;
}) {
  if (list === null || (catalog === null && customer === null)) return null;
  return (
    <span className="block text-xs text-zinc-500" data-testid="discount-steps">
      List <DecimalText value={list} />
      {catalog !== null && (
        <>
          {" "}
          · −<DecimalText value={catalog} />% campaign
        </>
      )}
      {customer !== null && (
        <>
          {" "}
          · −<DecimalText value={customer} />% customer
        </>
      )}
    </span>
  );
}
