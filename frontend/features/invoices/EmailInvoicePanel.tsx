"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Notice } from "@/components/ui/Notice";
import { TextAreaField, TextField } from "@/components/ui/Field";
import { apiFetch } from "@/lib/api/client";
import type { InvoiceEmailDraft, InvoiceEmailLog } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatDay } from "@/lib/timestamps";

const CONTROLS = ["to", "subject", "note"] as const;

/** What a failed attempt means to the person, in plain words (the code is the backend's short word, never the provider's text). */
export function failureText(code: string | null): string {
  switch (code) {
    case "rejected":
      return "The email address was refused. Check it and try again.";
    case "rate_limited":
    case "unavailable":
      return "The email service did not accept it just now. Try again in a moment.";
    default:
      return "Email sending is not working right now. Download the PDF and send it yourself, or try again later.";
  }
}

const STATUS_TEXT: Record<string, string> = { sent: "Sent", queued: "On its way", failed: "Not sent" };

/**
 * Email an ISSUED invoice to the customer: the invoice's own PDF is attached, the email comes from the organization's name and replies go
 * to the organization. The address, the subject and the message start from the draft (the customer's address and the organization's
 * pre-made text from Settings, filled in with this invoice's facts) and can be changed before sending; every attempt, including the ones that failed, is listed
 * below. Only the people who do the books see the form; anyone who can read the invoice sees the list.
 */
export function EmailInvoicePanel({
  invoiceId,
  kind = "invoice",
  draft,
  canSend,
  emails,
  timeZone,
  bare = false,
}: {
  /** The invoice's id, or (for a receipt) the id of the order that was paid at the counter. */
  invoiceId: string;
  kind?: "invoice" | "receipt";
  draft: InvoiceEmailDraft;
  canSend: boolean;
  emails: InvoiceEmailLog[];
  timeZone: string | null;
  /** Shown inside a pop-up window, which has the title and the frame already. */
  bare?: boolean;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const receipt = kind === "receipt";
  const noun = receipt ? "receipt" : "invoice";
  const { pending, error, run } = useMutation();
  const [to, setTo] = useState(draft.to);
  const [subject, setSubject] = useState(draft.subject);
  const [note, setNote] = useState(draft.message);
  const [result, setResult] = useState<InvoiceEmailLog | null>(null);
  const problems = problemsFrom(error, CONTROLS);
  // A structured refusal ({code, message}) carries its own wording (an invoice that is not issued, the daily limit).
  const general = error !== null && error.kind !== "validation" ? [error.message] : problems.general;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setResult(null);
    const sent = await run(() => apiFetch<InvoiceEmailLog>(orgId, receipt ? `/invoices/receipts/${invoiceId}/email` : `/invoices/${invoiceId}/email`, { method: "POST", body: { to, subject: subject.trim() === "" ? null : subject, note: note.trim() === "" ? null : note } }));
    if (sent === null) return;
    setResult(sent);
    if (sent.status !== "failed") setNote(draft.message); // ready for the next one: the pre-made text again
    router.refresh(); // the list below is read again from the server
  }

  return (
    <section aria-label={`Email the ${noun}`} data-testid={`email-${noun}`} className={bare ? "flex flex-col gap-3" : "panel flex max-w-3xl flex-col gap-3"}>
      {!bare && <h2 className="text-lg font-semibold">Email the invoice</h2>}
      {canSend ? (
        <form onSubmit={submit} noValidate aria-label={`Send ${noun} by email`} className="flex flex-col gap-3">
          <TextField label="Send to" name="to" value={to} onChange={setTo} inputMode="email" autoComplete="off" error={problems.byField.to} />
          {draft.to === "" && (
            <p className="text-xs text-secondary" data-testid="no-customer-email">
              This customer has no email address on the invoice. Type one above.
            </p>
          )}
          <TextField label="Subject" name="subject" value={subject} onChange={setSubject} autoComplete="off" error={problems.byField.subject} />
          <TextAreaField label="Message (optional)" name="note" value={note} onChange={setNote} rows={6} error={problems.byField.note} />
          <p className="text-xs text-secondary">
            {receipt
              ? "The receipt is attached as a PDF, after what was paid and how. "
              : "The invoice is attached as a PDF, after the invoice number, amount, due date and payment details. "}
            The email comes from your organization&apos;s name and replies go to your organization.
          </p>
          <ErrorSummary messages={general} />
          {result !== null &&
            (result.status === "failed" ? (
              <Notice tone="warning" testId="email-failed">
                {failureText(result.error)}
              </Notice>
            ) : (
              <Notice testId="email-sent">{receipt ? "Receipt" : "Invoice"} sent to {result.to_email}.</Notice>
            ))}
          <div>
            <Button type="submit" disabled={pending || to.trim() === ""} data-testid="send-invoice-email">
              {pending ? "Sending…" : `Send ${noun} by email`}
            </Button>
          </div>
        </form>
      ) : (
        <p className="text-sm text-secondary">{receipt ? "Only people who can change records can email receipts." : "Only owners, admins and accountants can email invoices."}</p>
      )}
      {emails.length === 0 ? (
        <p className="text-sm text-secondary" data-testid="no-invoice-emails">
          Not emailed yet.
        </p>
      ) : (
        <ul className="flex flex-col gap-1 text-sm" data-testid="invoice-emails">
          {emails.map((email) => (
            <li key={email.id} data-testid="invoice-email" className="flex flex-wrap gap-x-3">
              <span className="font-medium">{STATUS_TEXT[email.status] ?? email.status}</span>
              <span>{email.to_email}</span>
              <span className="text-secondary">
                {formatDay(email.created_at, timeZone)} · {email.sent_by_name ?? "not recorded"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
