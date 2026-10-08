"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, TextField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { Incoming } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { trimQuantity } from "@/lib/decimal";

const CONTROLS = ["quantity", "expected_on", "supplier", "reference"] as const;
const DATE = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";

const STATES: Record<Incoming["state"], string> = {
  expected: "Expected",
  partially_received: "Partly received",
  received: "Received",
  cancelled: "Cancelled",
};

/**
 * Stock on its way for one product: what was ordered, from whom and when it is expected. Incoming is never on hand:
 * a person receives it (all of it, or a part), and only then does it count. A receipt does not hand anything to a
 * waiting backorder by itself; that is a separate, confirmed step.
 */
export function IncomingPanel({ itemId, unit, incoming, canWrite }: { itemId: string; unit: string; incoming: Incoming[]; canWrite: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [form, setForm] = useState({ quantity: "", expected_on: "", supplier: "", reference: "" });
  const [receiving, setReceiving] = useState<Record<string, string>>({});
  const problems = problemsFrom(error, CONTROLS);
  const set = (key: keyof typeof form) => (value: string) => setForm((current) => ({ ...current, [key]: value }));

  async function create(event: FormEvent) {
    event.preventDefault();
    const body = {
      item_id: itemId,
      quantity: form.quantity.trim(),
      expected_on: blankToNull(form.expected_on),
      supplier: blankToNull(form.supplier),
      reference: blankToNull(form.reference),
    };
    const created = await run(() => apiFetch<Incoming>(orgId, "/inventory/incoming", { method: "POST", body }));
    if (created === null) return;
    setForm({ quantity: "", expected_on: "", supplier: "", reference: "" });
    router.refresh();
  }

  async function receive(row: Incoming) {
    const quantity = (receiving[row.id] ?? "").trim();
    const body = quantity === "" ? {} : { quantity };
    const received = await run(() => apiFetch<Incoming>(orgId, `/inventory/incoming/${row.id}/receive`, { method: "POST", body }));
    if (received === null) return;
    setReceiving((current) => ({ ...current, [row.id]: "" }));
    router.refresh();
  }

  async function cancel(row: Incoming) {
    const cancelled = await run(() => apiFetch<Incoming>(orgId, `/inventory/incoming/${row.id}/cancel`, { method: "POST" }));
    if (cancelled !== null) router.refresh();
  }

  return (
    <section aria-label="Incoming stock" data-testid="incoming-panel" className="flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Incoming</h2>
      {incoming.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-incoming">
          Nothing on its way.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Expected</th>
              <th className="py-1 pr-4">Unit</th>
              <th className="py-1 pr-4 text-right">Ordered</th>
              <th className="py-1 pr-4 text-right">Received</th>
              <th className="py-1 pr-4">Supplier</th>
              <th className="py-1 pr-4">Reference</th>
              <th className="py-1 pr-4">State</th>
              {canWrite && <th className="py-1">Receive</th>}
            </tr>
          </thead>
          <tbody>
            {incoming.map((row) => (
              <tr key={row.id} data-testid="incoming-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                <td className="py-1 pr-4">{row.expected_on ?? <span className="text-zinc-500">not given</span>}</td>
                <td className="py-1 pr-4">{unit}</td>
                <td className="py-1 pr-4 text-right" data-testid="incoming-ordered">
                  {trimQuantity(row.quantity)}
                </td>
                <td className="py-1 pr-4 text-right">
                  {trimQuantity(row.received)}
                </td>
                <td className="py-1 pr-4">{row.supplier}</td>
                <td className="py-1 pr-4">{row.reference}</td>
                <td className="py-1 pr-4">{STATES[row.state]}</td>
                {canWrite && (
                  <td className="py-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        aria-label="Quantity received"
                        inputMode="decimal"
                        placeholder={trimQuantity(row.remaining)}
                        value={receiving[row.id] ?? ""}
                        onChange={(event) => setReceiving((current) => ({ ...current, [row.id]: event.target.value }))}
                        className="w-24 rounded border border-zinc-400 px-2 py-1 dark:bg-zinc-900"
                      />
                      <Button type="button" disabled={pending} onClick={() => void receive(row)} data-testid="receive-incoming">
                        Receive
                      </Button>
                      <ConfirmButton
                        label="Cancel"
                        question="Will the rest of this delivery not come? Units already received stay on hand."
                        confirmLabel="Yes, cancel the rest"
                        disabled={pending}
                        onConfirm={() => void cancel(row)}
                        testId="cancel-incoming"
                      />
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canWrite && (
        <form onSubmit={create} noValidate aria-label="Record incoming stock" className="flex flex-col gap-3">
          <h3 className="font-medium">Record a delivery on its way</h3>
          <DecimalField label={`Quantity (unit: ${unit})`} name="quantity" value={form.quantity} onChange={set("quantity")} error={problems.byField.quantity} />
          <label className="flex flex-col gap-1 text-sm font-medium">
            Expected on (optional)
            <input type="date" name="expected_on" value={form.expected_on} onChange={(event) => set("expected_on")(event.target.value)} className={DATE} />
          </label>
          <TextField label="Supplier (optional)" name="supplier" value={form.supplier} onChange={set("supplier")} error={problems.byField.supplier} autoComplete="off" />
          <TextField label="Reference (optional)" name="reference" value={form.reference} onChange={set("reference")} error={problems.byField.reference} autoComplete="off" hint="For example the purchase order number." />
          <ErrorSummary messages={problems.general} />
          <div>
            <Button type="submit" disabled={pending} data-testid="submit-incoming">
              {pending ? "Saving…" : "Record incoming"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
