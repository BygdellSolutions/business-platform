"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, TextField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { ItemDiscount } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["percent", "starts_on", "ends_on", "note"] as const;
const DATE = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";

/**
 * Temporary discounts of one catalog item: a percentage off for a period. A discount applies by itself on the days
 * of its period and stops afterwards; the item's own price never changes. Periods never overlap (the backend
 * refuses an overlap and says so next to the start date). Owners and admins manage them; everyone sees them.
 */
export function ItemDiscounts({ itemId, discounts, canManage }: { itemId: string; discounts: ItemDiscount[]; canManage: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [percent, setPercent] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [note, setNote] = useState("");
  const problems = problemsFrom(error, CONTROLS);

  async function add(event: FormEvent) {
    event.preventDefault();
    const body = { percent, starts_on: startsOn, ends_on: blankToNull(endsOn), note: blankToNull(note) };
    const created = await run(() => apiFetch<ItemDiscount>(orgId, `/items/${itemId}/discounts`, { method: "POST", body }));
    if (created === null) return;
    setPercent("");
    setStartsOn("");
    setEndsOn("");
    setNote("");
    router.refresh();
  }

  async function remove(id: string) {
    const removed = await run(() => apiFetch<null>(orgId, `/items/${itemId}/discounts/${id}`, { method: "DELETE" }));
    if (removed !== null) router.refresh();
  }

  return (
    <section aria-label="Discounts" data-testid="item-discounts" className="flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Campaign discounts</h2>
      {discounts.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-discounts">
          No discounts.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Discount</th>
              <th className="py-1 pr-4">From</th>
              <th className="py-1 pr-4">To</th>
              <th className="py-1 pr-4">Note</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {discounts.map((discount) => (
              <tr key={discount.id} data-testid="discount-row" className="border-b border-zinc-200 dark:border-zinc-800">
                <td className="py-1 pr-4">
                  −<DecimalText value={discount.percent} /> %
                </td>
                <td className="py-1 pr-4">{discount.starts_on}</td>
                <td className="py-1 pr-4">{discount.ends_on ?? <span className="text-zinc-500">until removed</span>}</td>
                <td className="py-1 pr-4">{discount.note}</td>
                <td className="py-1">
                  {canManage && (
                    <ConfirmButton label="Remove" question="Remove this discount?" confirmLabel="Yes, remove" disabled={pending} onConfirm={() => void remove(discount.id)} testId="remove-discount" />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canManage && (
        <form onSubmit={add} noValidate aria-label="Add discount" className="flex flex-col gap-3">
          <h3 className="font-medium">Add a discount</h3>
          <DecimalField label="Discount %" name="percent" value={percent} onChange={setPercent} error={problems.byField.percent} />
          <label className="flex flex-col gap-1 text-sm font-medium">
            From
            <input type="date" name="starts_on" value={startsOn} onChange={(event) => setStartsOn(event.target.value)} className={DATE} />
            {problems.byField.starts_on && <span className="text-sm font-normal text-red-700 dark:text-red-300" data-testid="error-starts_on">{problems.byField.starts_on.join(" ")}</span>}
          </label>
          <label className="flex flex-col gap-1 text-sm font-medium">
            To (leave empty for no end)
            <input type="date" name="ends_on" value={endsOn} onChange={(event) => setEndsOn(event.target.value)} className={DATE} />
          </label>
          <TextField label="Note" name="note" value={note} onChange={setNote} error={problems.byField.note} autoComplete="off" />
          <ErrorSummary messages={problems.general} />
          <div>
            <Button type="submit" disabled={pending} data-testid="add-discount">
              {pending ? "Saving…" : "Add discount"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
