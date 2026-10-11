"use client";

import Link from "next/link";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { Notice } from "@/components/ui/Notice";
import { useEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import { DownloadPdf } from "@/features/invoices/DownloadPdf";
import { apiFetch } from "@/lib/api/client";
import type { DeliveryLine } from "@/lib/api/errors";
import type { Invoice, InvoiceStateOfOrder, PaymentMethod, Transaction } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";

type Action = "invoice" | "pay" | "reopen" | "cancel" | "wait";

const PROGRESS: Record<Action, string> = { invoice: "Invoicing…", pay: "Recording payment…", reopen: "Reopening…", cancel: "Cancelling…", wait: "Saving…" };
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
export function LifecycleBar({ canInvoice = true, invoiceState = "none" }: { canInvoice?: boolean; invoiceState?: InvoiceStateOfOrder["state"] }) {
  const orgId = useOrgId();
  const { transaction, stock, busy, editorsOpen, mutate, report, announce } = useEditor();
  const [running, setRunning] = useState<Action | null>(null);
  const [choosingMethod, setChoosingMethod] = useState(false);
  const [invoiced, setInvoiced] = useState<{ id: string } | "later" | "waiting" | null>(null);
  // Invoicing by delivery (owner, 2026-10-11): not everything is delivered and nobody has chosen yet.
  const [choice, setChoice] = useState<DeliveryLine[] | null>(null);
  const blocked = busy || editorsOpen > 0;
  const walkIn = transaction.billing_customer.walk_in === true;
  // Paying at the counter for more than is in stock is allowed (the rest is backordered), but never by accident:
  // the person at the till sees it before choosing how it was paid (decided by the owner 2026-10-11).
  const short = stock.filter((demand) => demand.shortage !== "0.000");

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
    await putOnInvoice(false);
  }

  /** The order (completed) onto the customer's draft invoice; `partial`: the person chose to invoice what is delivered. */
  async function putOnInvoice(partial: boolean) {
    setRunning("invoice");
    const draft = await mutate(() =>
      apiFetch<Invoice>(orgId, "/invoices/for-order", { method: "POST", body: partial ? { transaction_id: transaction.id, partial: true } : { transaction_id: transaction.id } }),
    );
    setRunning(null);
    if (draft === null) return;
    if (draft.ok) {
      setChoice(null);
      setInvoiced({ id: draft.data.id });
    } else if (draft.error.kind === "conflict" && draft.error.code === "partial_delivery" && draft.error.deliveryLines) {
      setChoice(draft.error.deliveryLines);
    } else explain(draft.error);
  }

  /** "Wait until everything is delivered": nothing is invoiced now; the whole order goes onto a draft when it is. */
  async function waitForDelivery() {
    setRunning("wait");
    const held = await mutate(() => apiFetch<null>(orgId, "/invoices/holds", { method: "POST", body: { transaction_id: transaction.id } }));
    setRunning(null);
    if (held === null) return;
    if (held.ok) {
      setChoice(null);
      setInvoiced("waiting");
    } else explain(held.error);
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
        {status === "draft" && choosingMethod && short.length > 0 && (
          <div className="basis-full">
          <Notice tone="warning" testId="pay-now-shortage">
            <p className="font-medium">Not everything is in stock.</p>
            {short.map((demand) => {
              const line = transaction.lines.find((entry) => entry.item_id === demand.item_id);
              return (
                <p key={demand.item_id}>
                  {line?.description}: {trimQuantity(demand.available)} of {trimQuantity(demand.requested)} in stock (unit: {line?.unit}). The customer
                  pays for all {trimQuantity(demand.requested)} now, takes {trimQuantity(demand.available)} today and gets {trimQuantity(demand.shortage)}{" "}
                  when they arrive.
                </p>
              );
            })}
          </Notice>
          </div>
        )}
        {status === "draft" && !walkIn && (
          <Button type="button" disabled={blocked} onClick={() => void invoice()} data-testid="invoice-order">
            {running === "invoice" ? PROGRESS.invoice : canInvoice ? "Invoice" : "Complete for invoicing"}
          </Button>
        )}
        {status === "completed" && !paid && canInvoice && !walkIn && choice === null && invoiced === null && (invoiceState === "none" || invoiceState === "waiting" || invoiceState === "partly") && (
          <Button type="button" variant="primary" disabled={blocked} onClick={() => void putOnInvoice(invoiceState === "waiting")} data-testid="invoice-completed-order">
            {running === "invoice" ? PROGRESS.invoice : invoiceState === "waiting" ? "Invoice what is delivered now" : "Invoice"}
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
      {choice !== null && status === "completed" && (
        <Notice tone="warning" testId="partial-delivery-choice">
          <p className="font-medium">Not everything on this order has been delivered.</p>
          <ul className="my-1 list-disc pl-5">
            {choice.map((line, index) => (
              <li key={index}>
                {line.description}: {trimQuantity(line.delivered)} of {trimQuantity(line.ordered)} delivered, {trimQuantity(line.backordered)} backordered (unit: {line.unit})
              </li>
            ))}
          </ul>
          <p>Invoice what is delivered now (the rest is invoiced when it is delivered), or wait and invoice the whole order when everything is delivered?</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button type="button" variant="primary" disabled={blocked || running !== null} onClick={() => void putOnInvoice(true)} data-testid="invoice-delivered-now">
              {running === "invoice" ? PROGRESS.invoice : "Invoice what is delivered now"}
            </Button>
            <Button type="button" disabled={blocked || running !== null} onClick={() => void waitForDelivery()} data-testid="wait-for-delivery">
              {running === "wait" ? PROGRESS.wait : "Wait until everything is delivered"}
            </Button>
          </div>
        </Notice>
      )}
      {invoiceState === "waiting" && invoiced === null && choice === null && status === "completed" && (
        <p className="text-sm text-secondary" data-testid="waiting-notice">
          Waiting until everything is delivered: the whole order goes onto a draft invoice when the last units are delivered.
        </p>
      )}
      {invoiced !== null && status === "completed" && (
        <p className="text-sm" data-testid="invoiced-notice">
          {invoiced === "later" ? (
            "Completed. An owner, admin or accountant puts it on an invoice."
          ) : invoiced === "waiting" ? (
            "Waiting until everything is delivered: the whole order goes onto a draft invoice when the last units are delivered."
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
