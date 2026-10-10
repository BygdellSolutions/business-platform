import type { TransactionStatus } from "@/lib/api/types";

const LOOK: Record<TransactionStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
  completed: { label: "Completed", className: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" },
  cancelled: { label: "Cancelled", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" },
};
const PAID = { label: "Paid", className: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" };

/** The lifecycle status of a transaction, as the server reports it. Presentation only. */
export function TransactionStatusBadge({ status, paid = false }: { status: TransactionStatus; paid?: boolean }) {
  // A completed order paid at the counter reads "Paid" (its status is still "completed").
  const look = status === "completed" && paid ? PAID : LOOK[status];
  return (
    <span data-testid="tx-status" data-status={status} data-paid={paid ? "true" : undefined} className={`rounded px-2 py-0.5 text-sm font-medium ${look.className}`}>
      {look.label}
    </span>
  );
}
