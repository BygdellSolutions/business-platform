import type { Invoice, InvoiceStatus } from "@/lib/api/types";

const DRAFT = { label: "Draft", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" };
const ISSUED = { label: "Issued", className: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" };
const PAYMENT: Record<NonNullable<Invoice["payment_status"]>, { label: string; className: string }> = {
  unpaid: { label: "Unpaid", className: "bg-sky-100 text-sky-900 dark:bg-sky-950 dark:text-sky-200" },
  partially_paid: { label: "Partially paid", className: "bg-violet-100 text-violet-900 dark:bg-violet-950 dark:text-violet-200" },
  paid: { label: "Paid", className: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" },
};

/**
 * Where an invoice stands, as the server reports it: a draft is "Draft"; an issued invoice says how far it is paid
 * (Unpaid, Partially paid, Paid). Without a payment state (an older answer) an issued invoice is "Issued".
 */
export function InvoiceStatusBadge({ status, paymentStatus = null }: { status: InvoiceStatus; paymentStatus?: Invoice["payment_status"] }) {
  const look = status === "draft" ? DRAFT : paymentStatus ? PAYMENT[paymentStatus] : ISSUED;
  return (
    <span
      data-testid="invoice-status"
      data-status={status}
      data-payment={paymentStatus ?? undefined}
      className={`rounded px-2 py-0.5 text-sm font-medium ${look.className}`}
    >
      {look.label}
    </span>
  );
}
