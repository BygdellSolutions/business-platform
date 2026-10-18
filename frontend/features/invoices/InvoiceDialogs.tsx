"use client";

import { useState } from "react";

import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { EmailInvoicePanel } from "@/features/invoices/EmailInvoicePanel";
import { PaymentsPanel } from "@/features/invoices/PaymentsPanel";
import type { Invoice, InvoiceEmailDraft, InvoiceEmailLog } from "@/lib/api/types";

const ZERO = /^0*(?:\.0*)?$/;

/**
 * The two things done to an issued invoice besides downloading it, as buttons beside "Download PDF" that open pop-up windows: "Send invoice by
 * email" (the address, subject and message, and what was sent before) and "Record payment" (the payments, refunds and reversals). The windows
 * hold the very same forms as before; only where they live changed. Whoever may not do the thing still opens a window, which then shows the
 * history read-only.
 */
export function InvoiceDialogs({
  invoice,
  canMutate,
  emailDraft,
  emails,
  today,
  timeZone,
}: {
  invoice: Invoice;
  canMutate: boolean;
  emailDraft: InvoiceEmailDraft;
  emails: InvoiceEmailLog[];
  today: string;
  timeZone: string | null;
}) {
  const [open, setOpen] = useState<"email" | "payment" | null>(null);
  if (invoice.status !== "issued") return null;

  const owesRefund = !ZERO.test(invoice.refund_due_amount ?? "0.00");
  const owesPayment = !ZERO.test(invoice.outstanding_amount ?? "0.00");
  const paymentLabel = canMutate ? (owesRefund ? "Record refund" : owesPayment ? "Record payment" : "Payments") : "Payments";
  const emailLabel = canMutate ? "Send invoice by email" : "Emails sent";
  const close = () => setOpen(null);

  return (
    <>
      <Button type="button" onClick={() => setOpen("email")} data-testid="open-email">
        {emailLabel}
      </Button>
      <Button type="button" onClick={() => setOpen("payment")} data-testid="open-payments">
        {paymentLabel}
      </Button>
      <Modal open={open === "email"} onClose={close} title="Send invoice by email" testId="email-dialog">
        <EmailInvoicePanel key={`email-${invoice.id}`} invoiceId={invoice.id} draft={emailDraft} canSend={canMutate} emails={emails} timeZone={timeZone} bare />
      </Modal>
      <Modal open={open === "payment"} onClose={close} title={paymentLabel} testId="payments-dialog">
        <PaymentsPanel key={`payments-${invoice.id}-${invoice.payments.length}-${invoice.credit_notes.length}`} invoice={invoice} canRecord={canMutate} today={today} timeZone={timeZone} bare />
      </Modal>
    </>
  );
}
