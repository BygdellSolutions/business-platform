"use client";

import Link from "next/link";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { DecimalText } from "@/components/ui/DecimalText";
import { DiscountSteps } from "@/components/ui/DiscountSteps";
import { ServiceSummary } from "@/components/ui/ServiceSummary";
import { useEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import { LineEditor } from "@/features/transactions/LineEditor";
import { LineFields } from "@/features/transactions/LineFields";
import { apiFetch } from "@/lib/api/client";
import type { LineFulfillment, TransactionLine } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/timestamps";
import { trimQuantity } from "@/lib/decimal";

/**
 * One line, shown exactly as stored: its own description, unit, quantity, price and VAT (the
 * snapshot taken when it was added or last edited) and the amounts FastAPI calculated. Nothing
 * here looks up the catalog item; `item_id` is only a link.
 */
const FULFILLMENT_STATES: Record<LineFulfillment["state"], string> = {
  waiting_for_stock: "waiting for stock",
  partially_fulfilled: "partially fulfilled",
  ready_to_fulfill: "ready to fulfill",
  fulfilled: "fulfilled",
  cancelled: "cancelled",
};

export function LineRow({ line, ordinal }: { line: TransactionLine; ordinal: number }) {
  const orgId = useOrgId();
  const { transaction, timeZone, stock, fulfillment, readOnly, busy, mutate, report } = useEditor();
  const fulfilled = transaction.status === "completed" ? fulfillment.find((entry) => entry.transaction_line_id === line.id) : undefined;
  const demand = line.item_id && transaction.status === "draft" ? stock.find((entry) => entry.item_id === line.item_id) : undefined;
  const shortage = demand && demand.shortage !== "0.000" ? demand : undefined;
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // An open editor must not come back to life if the transaction stops being a draft and is
  // reopened later: adjust the state when that prop changes.
  const [wasReadOnly, setWasReadOnly] = useState(readOnly);
  if (readOnly !== wasReadOnly) {
    setWasReadOnly(readOnly);
    if (readOnly) setEditing(false);
  }

  async function remove() {
    setDeleting(true);
    // The version shown on screen is the one the user decided on.
    const result = await mutate(() =>
      apiFetch<void>(orgId, `/transactions/${transaction.id}/lines/${line.id}`, { method: "DELETE", ifMatch: line.version }),
    );
    setDeleting(false);
    if (result !== null && !result.ok) report(classify(result.error), "save");
  }

  if (editing && !readOnly) {
    return (
      <tr data-testid="line-editor-row" data-line-id={line.id}>
        <td colSpan={11} className="py-3">
          <LineEditor line={line} ordinal={ordinal} onClose={() => setEditing(false)} />
        </td>
      </tr>
    );
  }

  return (
    <>
    <tr data-testid="line-row" data-line-id={line.id} data-version={line.version} aria-busy={deleting || undefined} className="border-b border-zinc-200 align-top dark:border-zinc-800">
      <td className="py-1 pr-3">{ordinal}</td>
      <td className="py-1 pr-3" data-testid="line-description">
        {line.description}
        {line.kind === "service" && line.performed_at && (
          <ServiceSummary subject={line.subject_label} when={formatTimestamp(line.performed_at, timeZone)} by={line.performed_by_name} notes={line.notes} />
        )}
        {fulfilled && (
          <span className="block text-xs text-zinc-600 dark:text-zinc-400" data-testid="line-fulfillment">
            Delivered {trimQuantity(fulfilled.delivered)}
            {fulfilled.backordered !== "0.000" && (
              <>
                {" "}
                · Backordered {trimQuantity(fulfilled.backordered)} ({FULFILLMENT_STATES[fulfilled.state]}
                {fulfilled.fulfilled_later !== "0.000" ? `, ${trimQuantity(fulfilled.fulfilled_later)} delivered since` : ""})
              </>
            )}
          </span>
        )}
        {shortage && (
          <span className="block text-xs text-amber-800 dark:text-amber-300" data-testid="stock-warning">
            In stock for this line: {trimQuantity(shortage.available)} of {trimQuantity(shortage.requested)}
            {shortage.allocated !== "0.000" && ` (${trimQuantity(shortage.allocated)} on other drafts)`}. {trimQuantity(shortage.shortage)} will be backordered at
            completion.
            {shortage.incoming !== "0.000" && ` ${trimQuantity(shortage.incoming)} on its way.`}
          </span>
        )}
      </td>
      <td className="py-1 pr-3" data-testid="line-unit">{line.unit}</td>
      <td className="py-1 pr-3 text-right" data-testid="line-quantity"><DecimalText value={line.quantity} /></td>
      <td className="py-1 pr-3 text-right" data-testid="line-price">
        <DecimalText value={line.unit_price_ex_vat} />
        <DiscountSteps list={line.list_unit_price} catalog={line.catalog_discount_percent} customer={line.customer_discount_percent} line={line.line_discount_percent} />
      </td>
      <td className="py-1 pr-3 text-right" data-testid="line-vat-rate"><DecimalText value={line.vat_rate} /></td>
      <td className="py-1 pr-3 text-right" data-testid="line-net"><DecimalText value={line.net_amount} /></td>
      <td className="py-1 pr-3 text-right" data-testid="line-vat"><DecimalText value={line.vat_amount} /></td>
      <td className="py-1 pr-3 text-right" data-testid="line-gross"><DecimalText value={line.gross_amount} /></td>
      <td className="py-1 pr-3" data-testid="line-item">
        {line.item_id ? (
          <Link href={`/o/${orgId}/catalog/${line.item_id}`} className="underline">
            Catalog item
          </Link>
        ) : (
          <span className="text-zinc-500">Ad-hoc</span>
        )}
      </td>
      <td className="py-1">
        {!readOnly && (
          <span className="flex flex-wrap items-center gap-2">
            <Button type="button" disabled={busy} onClick={() => setEditing(true)} data-testid="edit-line">
              Edit
            </Button>
            <ConfirmButton
              label={deleting ? "Deleting…" : "Delete"}
              question="Delete this line?"
              confirmLabel="Yes, delete"
              disabled={busy}
              onConfirm={() => void remove()}
              testId="delete-line"
            />
          </span>
        )}
      </td>
    </tr>
    <LineFields line={line} ordinal={ordinal} />
    </>
  );
}
