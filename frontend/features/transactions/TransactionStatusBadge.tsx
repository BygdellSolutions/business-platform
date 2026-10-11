import type { InvoiceStateOfOrder, TransactionStatus } from "@/lib/api/types";

const LOOK: Record<TransactionStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "tone-warning" },
  completed: { label: "Completed", className: "tone-success" },
  cancelled: { label: "Cancelled", className: "tone-neutral" },
};
const PAID = { label: "Paid", className: "tone-success" };
/** A completed order (not paid at the counter) read by where its invoicing stands (owner, 2026-10-11). */
const INVOICING: Record<InvoiceStateOfOrder["state"], { label: string; className: string }> = {
  none: { label: "Not invoiced", className: "tone-orange" },
  waiting: { label: "Waiting for delivery", className: "tone-neutral" },
  draft: { label: "Invoice draft", className: "tone-info" },
  partly: { label: "Partly invoiced", className: "tone-purple" },
  invoiced: { label: "Invoiced", className: "tone-primary" },
};

/**
 * The lifecycle status of a transaction, as the server reports it. Presentation only. With `invoice` (where Invoicing
 * says the order stands), a completed order reads by its invoicing instead: Not invoiced, Waiting for delivery,
 * Invoice draft, Partly invoiced or Invoiced.
 * The lifecycle status itself is unchanged (it is not an invoicing state).
 */
export function TransactionStatusBadge({ status, paid = false, invoice }: { status: TransactionStatus; paid?: boolean; invoice?: InvoiceStateOfOrder["state"] }) {
  // A completed order paid at the counter reads "Paid" (its status is still "completed").
  const look = status !== "completed" ? LOOK[status] : paid ? PAID : invoice ? INVOICING[invoice] : LOOK.completed;
  return (
    <span data-testid="tx-status" data-status={status} data-paid={paid ? "true" : undefined} data-invoice={invoice} className={`badge ${look.className}`}>
      {look.label}
    </span>
  );
}
