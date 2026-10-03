import type { TransactionStatus } from "@/lib/api/types";

const LOOK: Record<TransactionStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" },
  completed: { label: "Completed", className: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200" },
  cancelled: { label: "Cancelled", className: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" },
};

/** The lifecycle status of a transaction, as the server reports it. Presentation only. */
export function TransactionStatusBadge({ status }: { status: TransactionStatus }) {
  const look = LOOK[status];
  return (
    <span data-testid="tx-status" data-status={status} className={`rounded px-2 py-0.5 text-sm font-medium ${look.className}`}>
      {look.label}
    </span>
  );
}
