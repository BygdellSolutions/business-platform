"use client";

import { useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { CreditNotesPanel } from "@/features/invoices/CreditNotesPanel";
import { EmailInvoicePanel } from "@/features/invoices/EmailInvoicePanel";
import { PaymentsPanel } from "@/features/invoices/PaymentsPanel";
import { ReturnsPanel } from "@/features/invoices/ReturnsPanel";
import type { Invoice, InvoiceEmailDraft, InvoiceEmailLog } from "@/lib/api/types";

const ZERO = /^0*(?:\.0*)?$/;

/**
 * What is done to an issued invoice besides downloading it, as buttons beside "Download PDF" that open pop-up windows: "Send invoice by
 * email" (the address, subject and message, and what was sent before), "Record payment" (the payments, refunds and reversals), "Returns" (the
 * return cases) and "Credit notes". The windows hold the very same forms as before; only where they live changed. Whoever may not do the
 * thing still opens a window, which then shows the history read-only (and Returns / Credit notes are only offered when there is something
 * to see or the person can start one). Approving a return, or "Create the credit note" on one, closes the Returns window and opens the Credit
 * notes window with that return's lines and reason filled in.
 */
export function InvoiceDialogs({
  invoice,
  canMutate,
  emailDraft,
  emails,
  today,
  timeZone,
  initialCreditReturn,
}: {
  invoice: Invoice;
  canMutate: boolean;
  emailDraft: InvoiceEmailDraft;
  emails: InvoiceEmailLog[];
  today: string;
  timeZone: string | null;
  /** An approved return to write the credit note for, named in the page address (`?credit_return=`): the Credit notes window opens on it. */
  initialCreditReturn?: string;
}) {
  const [open, setOpen] = useState<"email" | "payment" | "returns" | "credits" | null>(initialCreditReturn === undefined ? null : "credits");
  const [creditReturn, setCreditReturn] = useState<string | undefined>(initialCreditReturn);
  if (invoice.status !== "issued") return null;

  const owesRefund = !ZERO.test(invoice.refund_due_amount ?? "0.00");
  const owesPayment = !ZERO.test(invoice.outstanding_amount ?? "0.00");
  const paymentLabel = canMutate ? (owesRefund ? "Record refund" : owesPayment ? "Record payment" : "Payments") : "Payments";
  const emailLabel = canMutate ? "Send invoice by email" : "Emails sent";
  const showReturns = canMutate || invoice.returns.length > 0;
  const showCredits = canMutate || invoice.credit_notes.length > 0;
  const openReturns = invoice.returns.filter((item) => ["requested", "goods_received", "approved"].includes(item.state)).length;
  const close = () => setOpen(null);
  const creditThisReturn = (returnId: string) => {
    setCreditReturn(returnId);
    setOpen("credits");
  };

  return (
    <>
      <Button type="button" onClick={() => setOpen("email")} data-testid="open-email">
        {emailLabel}
      </Button>
      <Button type="button" onClick={() => setOpen("payment")} data-testid="open-payments">
        {paymentLabel}
      </Button>
      {showReturns && (
        <Button type="button" onClick={() => setOpen("returns")} data-testid="open-returns">
          {openReturns > 0 ? `Returns (${openReturns} open)` : "Returns"}
        </Button>
      )}
      {showCredits && (
        <Button type="button" onClick={() => setOpen("credits")} data-testid="open-credits">
          {invoice.credit_notes.length > 0 ? `Credit notes (${invoice.credit_notes.length})` : "Credit notes"}
        </Button>
      )}
      <Modal open={open === "email"} onClose={close} title="Send invoice by email" testId="email-dialog">
        <EmailInvoicePanel key={`email-${invoice.id}`} invoiceId={invoice.id} draft={emailDraft} canSend={canMutate} emails={emails} timeZone={timeZone} bare />
      </Modal>
      <Modal open={open === "payment"} onClose={close} title={paymentLabel} testId="payments-dialog">
        <PaymentsPanel key={`payments-${invoice.id}-${invoice.payments.length}-${invoice.credit_notes.length}`} invoice={invoice} canRecord={canMutate} today={today} timeZone={timeZone} bare />
      </Modal>
      {showReturns && (
        <Modal open={open === "returns"} onClose={close} title="Returns" testId="returns-dialog">
          <ReturnsPanel key={`returns-${invoice.id}-${invoice.returns.length}`} invoice={invoice} canHandle={canMutate} today={today} timeZone={timeZone} onCredit={creditThisReturn} bare />
        </Modal>
      )}
      {showCredits && (
        <Modal open={open === "credits"} onClose={close} title="Credit notes" testId="credits-dialog">
          <CreditNotesPanel
            // New when a credit note appears or a return changes state (approving one makes it creditable), so the form starts on the right return.
            key={`credits-${invoice.id}-${invoice.credit_notes.length}-${creditReturn ?? ""}-${invoice.returns.map((item) => `${item.id}:${item.state}`).join(",")}`}
            invoice={invoice}
            canCredit={canMutate}
            timeZone={timeZone}
            creditReturnId={creditReturn}
            bare
          />
        </Modal>
      )}
    </>
  );
}
