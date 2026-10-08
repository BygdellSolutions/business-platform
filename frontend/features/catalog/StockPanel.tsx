"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, SelectField, TextField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { ItemAvailability, Stock, StockMovement } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { formatTimestamp } from "@/lib/timestamps";

import { StockBadges } from "./StockBadges";
import { trimQuantity } from "@/lib/decimal";

const CONTROLS = ["kind", "quantity", "note"] as const;

const REASONS: Record<StockMovement["reason"], string> = {
  opening: "Opening count",
  adjustment: "Adjustment",
  receipt: "Goods received",
  delivery: "Delivered",
  return: "Returned",
};

const KINDS = [
  { value: "count", label: "Counted: there are" },
  { value: "add", label: "Add" },
  { value: "remove", label: "Remove" },
];

/**
 * The physical stock of a product that tracks it: the quantity on hand and the movements that explain it, newest
 * first. A member who may write records counts, adds or removes stock; every change after the opening count needs a
 * note saying why. Stock never goes below zero (the backend refuses and says how much is on hand).
 */
export function StockPanel({
  itemId,
  unit,
  stock,
  figures,
  canAdjust,
  timeZone,
}: {
  itemId: string;
  unit: string;
  stock: Stock;
  /** Committed, available and incoming next to on hand (null: not read). */
  figures: ItemAvailability | null;
  canAdjust: boolean;
  timeZone: string | null;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [kind, setKind] = useState("count");
  const [quantity, setQuantity] = useState("");
  const [note, setNote] = useState("");
  const problems = problemsFrom(error, CONTROLS);
  const opening = stock.movements.length === 0;

  async function adjust(event: FormEvent) {
    event.preventDefault();
    const body = { kind: opening ? "count" : kind, quantity: quantity.trim(), note: blankToNull(note) };
    const saved = await run(() => apiFetch<Stock>(orgId, `/items/${itemId}/stock`, { method: "POST", body }));
    if (saved === null) return;
    setQuantity("");
    setNote("");
    router.refresh();
  }

  return (
    <section aria-label="Stock" data-testid="stock-panel" className="flex max-w-3xl flex-col gap-3">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        Stock {figures && <StockBadges states={figures.states} />}
      </h2>
      <p className="text-sm">
        On hand:{" "}
        <span className="font-semibold" data-testid="on-hand">
          {trimQuantity(stock.on_hand)}
        </span>{" "}
        <span className="text-zinc-600 dark:text-zinc-400">· unit: {unit}</span>
        {figures && (
          <span className="text-zinc-600 dark:text-zinc-400" data-testid="stock-figures">
            {" "}
            · allocated to drafts {trimQuantity(figures.allocated)} · committed to backorders {trimQuantity(figures.committed)} · available {trimQuantity(figures.available)} ·
            incoming {trimQuantity(figures.incoming)}
          </span>
        )}
      </p>
      {stock.movements.length > 0 && (
        <details data-testid="stock-history-toggle">
          <summary className="cursor-pointer select-none text-sm text-zinc-700 underline dark:text-zinc-300">Show stock history ({stock.movements.length})</summary>
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">When</th>
                <th className="py-1 pr-4">What</th>
                <th className="py-1 pr-4 text-right">Change</th>
                <th className="py-1 pr-4 text-right">On hand after</th>
                <th className="py-1 pr-4">Note</th>
                <th className="py-1 pr-4">By</th>
              </tr>
            </thead>
            <tbody>
              {stock.movements.map((movement) => (
                <tr key={movement.id} data-testid="stock-movement" className="border-b border-zinc-200 dark:border-zinc-800">
                  <td className="py-1 pr-4">{formatTimestamp(movement.created_at, timeZone)}</td>
                  <td className="py-1 pr-4">{REASONS[movement.reason]}</td>
                  <td className="py-1 pr-4 text-right">
                    {movement.quantity_change.startsWith("-") ? "" : "+"}
                    {trimQuantity(movement.quantity_change)}
                  </td>
                  <td className="py-1 pr-4 text-right">
                    {trimQuantity(movement.quantity_after)}
                  </td>
                  <td className="py-1 pr-4">{movement.note}</td>
                  <td className="py-1 pr-4">{movement.created_by_name ?? <span className="text-zinc-500">not recorded</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      {canAdjust && (
        <form onSubmit={adjust} noValidate aria-label="Change stock" className="flex flex-col gap-3">
          <h3 className="font-medium">{opening ? "Record the opening stock" : "Change the stock"}</h3>
          {!opening && <SelectField label="Change" name="kind" value={kind} onChange={setKind} options={KINDS} error={problems.byField.kind} />}
          <DecimalField label={opening ? `Counted quantity (unit: ${unit})` : `Quantity (unit: ${unit})`} name="quantity" value={quantity} onChange={setQuantity} error={problems.byField.quantity} />
          <TextField
            label={opening ? "Note (optional)" : "Why (for example: counted, damaged, lost)"}
            name="note"
            value={note}
            onChange={setNote}
            error={problems.byField.note}
            autoComplete="off"
          />
          <ErrorSummary messages={problems.general} />
          <div>
            <Button type="submit" disabled={pending} data-testid="submit-stock">
              {pending ? "Saving…" : opening ? "Record opening stock" : "Save stock change"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
