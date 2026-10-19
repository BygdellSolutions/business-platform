import type { Invoice, InvoiceStatus } from "@/lib/api/types";

/** An open return case: one look wherever it is shown (the invoice's badges, the invoice list's Return column). */
export const RETURN_OPEN_CLASS = "badge tone-orange";

const DRAFT = { label: "Draft", className: "tone-warning" };
const ISSUED = { label: "Issued", className: "tone-info" };
const PAYMENT: Record<NonNullable<Invoice["payment_status"]>, { label: string; className: string }> = {
  unpaid: { label: "Unpaid", className: "tone-info" },
  partially_paid: { label: "Partially paid", className: "tone-purple" },
  paid: { label: "Paid", className: "tone-success" },
};

// Its own colour (teal) and a small envelope: no other badge on an invoice looks like it (grey is "Credited", orange "Return open").
const SENT = { label: "Sent", className: "tone-teal" };
const CREDITED = { label: "Credited", className: "tone-neutral" };
const PARTLY_CREDITED = { label: "Partly credited", className: "tone-neutral" };

/**
 * Where an invoice stands, as the server reports it: a draft is "Draft"; an issued invoice says how far it is paid
 * (Unpaid, Partially paid, Paid), or "Credited" when credit notes cancel all of it. A partly credited invoice shows its
 * payment state and a second "Partly credited" badge. Without a payment state (an older answer) it is "Issued".
 *
 * An invoice that has been emailed to the customer also gets a small "Sent" badge, but only while it is still waiting to be settled:
 * once it is paid or fully credited the "Sent" is dropped and the invoice says just "Paid" (or "Credited"). A draft is never "Sent".
 */
export function InvoiceStatusBadge({
  status,
  paymentStatus = null,
  creditStatus = null,
  openReturns = 0,
  emailed = false,
}: {
  status: InvoiceStatus;
  paymentStatus?: Invoice["payment_status"];
  creditStatus?: Invoice["credit_status"];
  openReturns?: number;
  emailed?: boolean;
}) {
  const look = status === "draft" ? DRAFT : creditStatus === "credited" ? CREDITED : paymentStatus ? PAYMENT[paymentStatus] : ISSUED;
  return (
    <span className="inline-flex gap-1 whitespace-nowrap">
      <span
        data-testid="invoice-status"
        data-status={status}
        data-payment={paymentStatus ?? undefined}
        data-credit={creditStatus ?? undefined}
        className={`badge ${look.className}`}
      >
        {look.label}
      </span>
      {status === "issued" && emailed && paymentStatus !== "paid" && creditStatus !== "credited" && (
        <span data-testid="invoice-sent" className={`badge ${SENT.className} gap-1`}>
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 6h18v12H3zM3 7l9 6 9-6" />
          </svg>
          {SENT.label}
        </span>
      )}
      {status === "issued" && creditStatus === "partly_credited" && (
        <span data-testid="credit-status" className={`badge ${PARTLY_CREDITED.className}`}>
          {PARTLY_CREDITED.label}
        </span>
      )}
      {status === "issued" && openReturns > 0 && (
        <span data-testid="return-open" className={RETURN_OPEN_CLASS}>
          Return open
        </span>
      )}
    </span>
  );
}
