"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { BACKORDER_STATES } from "@/features/catalog/backorder-labels";
import { apiFetch } from "@/lib/api/client";
import type { AllocationProposal, Backorder } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { trimQuantity } from "@/lib/decimal";


/**
 * The open backorders of one product, oldest first. When stock is on hand, "Propose" asks the backend how it would be
 * shared (oldest first); the person may change the quantities and then confirms. Nothing is delivered until then.
 */
export function BackordersPanel({ itemId, unit, backorders, canAllocate }: { itemId: string; unit: string; backorders: Backorder[]; canAllocate: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [onHand, setOnHand] = useState<string | null>(null);
  const problems = problemsFrom(error, []);

  async function propose() {
    const proposal = await run(() => apiFetch<AllocationProposal>(orgId, `/inventory/items/${itemId}/allocation`));
    if (proposal === null) return;
    setOnHand(proposal.on_hand);
    setQuantities(Object.fromEntries(proposal.proposals.map((entry) => [entry.fulfillment_id, entry.proposed === "0.000" ? "" : trimQuantity(entry.proposed)])));
  }

  async function confirm() {
    const allocations = Object.entries(quantities)
      .map(([fulfillment_id, quantity]) => ({ fulfillment_id, quantity: quantity.trim() }))
      .filter((entry) => entry.quantity !== "" && !/^0*(\.0*)?$/.test(entry.quantity));
    if (allocations.length === 0) return;
    const done = await run(() => apiFetch<Backorder[]>(orgId, `/inventory/items/${itemId}/allocation`, { method: "POST", body: { allocations } }));
    if (done === null) return;
    setQuantities({});
    setOnHand(null);
    router.refresh();
  }

  if (backorders.length === 0) return null;
  return (
    <section aria-label="Backorders" data-testid="backorders-panel" className="flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Backorders</h2>
      <table className="text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-300 dark:border-zinc-700">
            <th className="py-1 pr-4">Completed</th>
            <th className="py-1 pr-4">Customer</th>
            <th className="py-1 pr-4">Unit</th>
            <th className="py-1 pr-4 text-right">Waiting</th>
            <th className="py-1 pr-4">State</th>
            {canAllocate && <th className="py-1">Deliver now</th>}
          </tr>
        </thead>
        <tbody>
          {backorders.map((backorder) => (
            <tr key={backorder.fulfillment_id} data-testid="backorder-row" className="border-b border-zinc-200 dark:border-zinc-800">
              <td className="py-1 pr-4">
                <Link href={`/o/${orgId}/transactions/${backorder.transaction_id}`} className="underline">
                  Order {backorder.transaction_number} · {backorder.transaction_date}
                </Link>
              </td>
              <td className="py-1 pr-4">{backorder.customer_name}</td>
              <td className="py-1 pr-4">{unit}</td>
              <td className="py-1 pr-4 text-right" data-testid="backorder-waiting">
                {trimQuantity(backorder.remaining)}
              </td>
              <td className="py-1 pr-4" data-testid="backorder-state">
                {BACKORDER_STATES[backorder.state]}
              </td>
              {canAllocate && (
                <td className="py-1">
                  <input
                    aria-label={`Deliver now to ${backorder.customer_name ?? "this sale"}`}
                    inputMode="decimal"
                    value={quantities[backorder.fulfillment_id] ?? ""}
                    onChange={(event) => setQuantities((current) => ({ ...current, [backorder.fulfillment_id]: event.target.value }))}
                    className="w-24 rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
                    data-testid="allocation-quantity"
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {canAllocate && (
        <div className="flex flex-col gap-2">
          {onHand !== null && (
            <p className="text-sm text-zinc-600 dark:text-zinc-400" data-testid="allocation-proposed">
              Proposed oldest first from {trimQuantity(onHand)} on hand. Change the quantities if needed, then confirm.
            </p>
          )}
          <ErrorSummary messages={[...problems.general, ...Object.values(problems.byField).flat()]} />
          <div className="flex gap-3">
            <Button type="button" disabled={pending} onClick={() => void propose()} data-testid="propose-allocation">
              Propose (oldest first)
            </Button>
            <Button type="button" disabled={pending} onClick={() => void confirm()} data-testid="confirm-allocation">
              Confirm allocation
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
