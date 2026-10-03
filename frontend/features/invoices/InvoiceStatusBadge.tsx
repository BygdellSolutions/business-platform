import type { InvoiceStatus } from "@/lib/api/types";

const LOOK: Record<InvoiceStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
  issued: { label: "Issued", className: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" },
};

/** The status of an invoice, as the server reports it. Presentation only. */
export function InvoiceStatusBadge({ status }: { status: InvoiceStatus }) {
  const look = LOOK[status];
  return (
    <span data-testid="invoice-status" data-status={status} className={`rounded px-2 py-0.5 text-sm font-medium ${look.className}`}>
      {look.label}
    </span>
  );
}
