import type { TransactionStatus } from "@/lib/api/types";

const LOOK: Record<TransactionStatus, { label: string; className: string }> = {
  draft: { label: "Draft", className: "tone-warning" },
  completed: { label: "Completed", className: "tone-success" },
  cancelled: { label: "Cancelled", className: "tone-neutral" },
};
const PAID = { label: "Paid", className: "tone-success" };

/** The lifecycle status of a transaction, as the server reports it. Presentation only. */
export function TransactionStatusBadge({ status, paid = false }: { status: TransactionStatus; paid?: boolean }) {
  // A completed order paid at the counter reads "Paid" (its status is still "completed").
  const look = status === "completed" && paid ? PAID : LOOK[status];
  return (
    <span data-testid="tx-status" data-status={status} data-paid={paid ? "true" : undefined} className={`badge ${look.className}`}>
      {look.label}
    </span>
  );
}
