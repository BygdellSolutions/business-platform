import { DecimalText } from "@/components/ui/DecimalText";
import type { MoneyString, PercentString } from "@/lib/decimal";

/**
 * The discount layers of a line as stored: the list price, then the catalog discount, the customer discount and the
 * line's own discount. Each value is the backend's string; nothing is calculated here. Nothing is shown for a line
 * without layers.
 */
export function DiscountSteps({
  list,
  catalog,
  customer,
  line = null,
}: {
  list: MoneyString | null;
  catalog: PercentString | null;
  customer: PercentString | null;
  line?: PercentString | null;
}) {
  if (list === null || (catalog === null && customer === null && line === null)) return null;
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
      {line !== null && (
        <>
          {" "}
          · −<DecimalText value={line} />% discount
        </>
      )}
    </span>
  );
}
