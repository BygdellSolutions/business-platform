"use client";

import Link from "next/link";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { useEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import { DownloadPdf } from "@/features/invoices/DownloadPdf";
import { apiFetch } from "@/lib/api/client";
import type { Invoice, PaymentMethod, Transaction } from "@/lib/api/types";

type Action = "invoice" | "pay" | "reopen" | "cancel";

const PROGRESS: Record<Action, string> = { invoice: "Invoicing…", pay: "Recording payment…", reopen: "Reopening…", cancel: "Cancelling…" };
export const PAYMENT_METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "swish", label: "Swish" },
  { value: "card", label: "Card" },
  { value: "cash", label: "Cash" },
];

/**
 * What a draft order becomes (decided by the owner 2026-10-10): either PAID NOW at the counter (completed and paid in
 * one step, with a receipt, never invoiced) or INVOICED (completed and put on the customer's open draft invoice, or a
 * new one). There is no plain "complete": every finished order is paid or on its way to an invoice. A role that may
 * not make invoices completes the order and leaves the invoicing to an owner, admin or accountant.
 *
 * Which buttons appear is presentation; whether a step is allowed is FastAPI's decision, and a refused step is
 * explained and followed by a refresh. Each step carries the transaction's `version`, so a step decided on a stale
 * screen is refused instead of applied. The buttons wait while any editor is open.
 */
export function LifecycleBar({ canInvoice = true }: { canInvoice?: boolean }) {
  const orgId = useOrgId();
  const { transaction, busy, editorsOpen, mutate, report, announce } = useEditor();
  const [running, setRunning] = useState<Action | null>(null);
  const [choosingMethod, setChoosingMethod] = useState(false);
  const [invoiced, setInvoiced] = useState<{ id: string } | "later" | null>(null);
  const blocked = busy || editorsOpen > 0;
  const walkIn = transaction.billing_customer.walk_in === true;

  async function step(action: "reopen" | "cancel") {
    setRunning(action);
    const result = await mutate(() => apiFetch<Transaction>(orgId, `/transactions/${transaction.id}/${action}`, { method: "POST", ifMatch: transaction.version }));
    setRunning(null);
    if (result !== null && !result.ok) explain(result.error);
  }

  async function payNow(method: PaymentMethod) {
    setRunning("pay");
    const result = await mutate(() =>
      apiFetch<Transaction>(orgId, `/transactions/${transaction.id}/pay-now`, { method: "POST", ifMatch: transaction.version, body: { method } }),
    );
    setRunning(null);
    setChoosingMethod(false);
    if (result !== null && !result.ok) explain(result.error);
  }

  async function invoice() {
    setRunning("invoice");
    // Completing comes first (stock, required fields); the invoice is a second request to Invoicing. If that one is
    // refused, the order is completed and waits under "Ready to invoice", and the reason is shown.
    const completed = await mutate(() => apiFetch<Transaction>(orgId, `/transactions/${transaction.id}/complete`, { method: "POST", ifMatch: transaction.version }));
    if (completed === null || !completed.ok) {
      setRunning(null);
      if (completed !== null && !completed.ok) explain(completed.error);
      return;
    }
    if (!canInvoice) {
      setRunning(null);
      setInvoiced("later");
      return;
    }
    const draft = await mutate(() => apiFetch<Invoice>(orgId, "/invoices/for-order", { method: "POST", body: { transaction_id: transaction.id } }));
    setRunning(null);
    if (draft === null) return;
    if (draft.ok) setInvoiced({ id: draft.data.id });
    else explain(draft.error);
  }

  function explain(error: Parameters<typeof classify>[0]) {
    const failure = classify(error);
    if (failure.kind === "problems") announce(null); // the problems replace any earlier message below
    report(failure, "lifecycle");
  }

  const { status } = transaction;
  const paid = transaction.paid_at !== null;
  return (
    <section aria-label="Actions" data-testid="lifecycle" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        {status === "draft" && !choosingMethod && (
          <Button type="button" disabled={blocked} onClick={() => setChoosingMethod(true)} data-testid="pay-now">
            {running === "pay" ? PROGRESS.pay : "Paid now"}
          </Button>
        )}
        {status === "draft" && choosingMethod && (
          <span className="flex flex-wrap items-center gap-2" role="group" aria-label="How was it paid?" data-testid="payment-methods">
            <span className="text-sm">Paid by</span>
            {PAYMENT_METHODS.map((method) => (
              <Button key={method.value} type="button" disabled={blocked} onClick={() => void payNow(method.value)} data-testid={`pay-${method.value}`}>
                {method.label}
              </Button>
            ))}
            <button type="button" className="text-sm underline" onClick={() => setChoosingMethod(false)}>
              Back
            </button>
          </span>
        )}
        {status === "draft" && !walkIn && (
          <Button type="button" disabled={blocked} onClick={() => void invoice()} data-testid="invoice-order">
            {running === "invoice" ? PROGRESS.invoice : canInvoice ? "Invoice" : "Complete for invoicing"}
          </Button>
        )}
        {status === "completed" && !paid && (
          <Button type="button" disabled={blocked} onClick={() => void step("reopen")} data-testid="reopen">
            {running === "reopen" ? PROGRESS.reopen : "Reopen"}
          </Button>
        )}
        {(status === "draft" || (status === "completed" && !paid)) && (
          <ConfirmButton
            label={running === "cancel" ? PROGRESS.cancel : "Cancel order"}
            question="Cancel this order? This is final."
            confirmLabel="Yes, cancel it"
            disabled={blocked}
            onConfirm={() => void step("cancel")}
            testId="cancel"
          />
        )}
        {paid && (
          <span className="flex flex-wrap items-center gap-3 text-sm" data-testid="paid-at-counter">
            Paid by {PAYMENT_METHODS.find((method) => method.value === transaction.payment_method)?.label ?? transaction.payment_method} · Receipt{" "}
            {transaction.receipt_number_text}
            <DownloadPdf invoice={{ id: transaction.id, status: "issued" }} path={`/invoices/receipts/${transaction.id}/pdf`} label="Download receipt" />
          </span>
        )}
        {status === "cancelled" && <span className="text-sm text-zinc-500">No further actions.</span>}
      </div>
      {status === "draft" && walkIn && (
        <p className="text-sm text-zinc-500" data-testid="walk-in-hint">
          A walk-in customer pays at the counter; choose a named customer to invoice instead.
        </p>
      )}
      {invoiced !== null && status === "completed" && (
        <p className="text-sm" data-testid="invoiced-notice">
          {invoiced === "later" ? (
            "Completed. An owner, admin or accountant puts it on an invoice."
          ) : (
            <>
              Added to a draft invoice.{" "}
              <Link href={`/o/${orgId}/invoices/${invoiced.id}`} className="underline" data-testid="open-draft-invoice">
                Open the draft invoice
              </Link>
            </>
          )}
        </p>
      )}
      {editorsOpen > 0 && status !== "cancelled" && (
        <p data-testid="lifecycle-hint" className="text-sm text-zinc-500">
          Save or cancel your open edit first.
        </p>
      )}
    </section>
  );
}
