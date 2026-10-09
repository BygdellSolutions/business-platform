import type { Invoice, InvoiceStatus } from "@/lib/api/types";

const DRAFT = { label: "Draft", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" };
const ISSUED = { label: "Issued", className: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" };
const PAYMENT: Record<NonNullable<Invoice["payment_status"]>, { label: string; className: string }> = {
  unpaid: { label: "Unpaid", className: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" },
  partially_paid: { label: "Partially paid", className: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200" },
  paid: { label: "Paid", className: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" },
};

const CREDITED = { label: "Credited", className: "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200" };
const PARTLY_CREDITED = { label: "Partly credited", className: "bg-zinc-200 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-200" };

/**
 * Where an invoice stands, as the server reports it: a draft is "Draft"; an issued invoice says how far it is paid
 * (Unpaid, Partially paid, Paid), or "Credited" when credit notes cancel all of it. A partly credited invoice shows its
 * payment state and a second "Partly credited" badge. Without a payment state (an older answer) it is "Issued".
 */
export function InvoiceStatusBadge({
  status,
  paymentStatus = null,
  creditStatus = null,
  openReturns = 0,
}: {
  status: InvoiceStatus;
  paymentStatus?: Invoice["payment_status"];
  creditStatus?: Invoice["credit_status"];
  openReturns?: number;
}) {
  const look = status === "draft" ? DRAFT : creditStatus === "credited" ? CREDITED : paymentStatus ? PAYMENT[paymentStatus] : ISSUED;
  return (
    <span className="inline-flex flex-wrap gap-1">
      <span
        data-testid="invoice-status"
        data-status={status}
        data-payment={paymentStatus ?? undefined}
        data-credit={creditStatus ?? undefined}
        className={`rounded px-2 py-0.5 text-sm font-medium ${look.className}`}
      >
        {look.label}
      </span>
      {status === "issued" && creditStatus === "partly_credited" && (
        <span data-testid="credit-status" className={`rounded px-2 py-0.5 text-sm font-medium ${PARTLY_CREDITED.className}`}>
          {PARTLY_CREDITED.label}
        </span>
      )}
      {status === "issued" && openReturns > 0 && (
        <span data-testid="return-open" className="rounded bg-orange-100 px-2 py-0.5 text-sm font-medium text-orange-900 dark:bg-orange-950 dark:text-orange-200">
          Return open
        </span>
      )}
    </span>
  );
}
