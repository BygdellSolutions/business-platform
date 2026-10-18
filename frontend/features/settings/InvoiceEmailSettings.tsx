"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField, TextField } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { Organization, OrganizationUpdate } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["invoice_email_subject", "invoice_email_message"] as const;

/** For the reader's help only: the backend knows the real list and refuses any other word in curly brackets, saying which. */
const PLACEHOLDERS: [string, string][] = [
  ["{customer}", "the customer's name"],
  ["{invoice_number}", "the invoice number"],
  ["{amount}", "the amount to pay, with currency"],
  ["{due_date}", "the due date"],
  ["{payment_reference}", "the payment reference (OCR)"],
  ["{organization}", "your organization's name"],
];

/**
 * The pre-made subject and message of an invoice email, written once here and filled in on every invoice's send form, where the person can
 * still change them before sending. Words in curly brackets are replaced by the invoice's own facts. Left empty, the email gets the built-in
 * subject and no message. Owners and admins edit; everyone else reads.
 */
export function InvoiceEmailSettings({ organization, canEdit }: { organization: Organization; canEdit: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [record, setRecord] = useState(organization);
  const [subject, setSubject] = useState(organization.invoice_email_subject ?? "");
  const [message, setMessage] = useState(organization.invoice_email_message ?? "");
  const [notice, setNotice] = useState<"saved" | "unchanged" | null>(null);
  const problems = problemsFrom(error, CONTROLS);

  async function save(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    // Only what changed is sent, so a save never overwrites a field it did not touch.
    const body: OrganizationUpdate = {};
    if (blankToNull(subject) !== record.invoice_email_subject) body.invoice_email_subject = blankToNull(subject);
    if (blankToNull(message) !== record.invoice_email_message) body.invoice_email_message = blankToNull(message);
    if (Object.keys(body).length === 0) return setNotice("unchanged");
    const saved = await run(() => apiFetch<Organization>(orgId, "/organization", { method: "PATCH", body }));
    if (saved === null) return;
    setRecord(saved);
    setSubject(saved.invoice_email_subject ?? "");
    setMessage(saved.invoice_email_message ?? "");
    setNotice("saved");
    router.refresh();
  }

  return (
    <section aria-label="Invoice email" data-testid="invoice-email-settings" className="panel flex max-w-xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Invoice email</h2>
      <p className="text-sm text-secondary">
        The text an invoice email starts with. It is filled in on each invoice&apos;s send form, where you can still change it before sending. Leave
        both empty to use the built-in subject and no message.
      </p>
      {canEdit ? (
        <form onSubmit={save} noValidate aria-label="Invoice email text" className="flex flex-col gap-3">
          <TextField label="Subject" name="invoice_email_subject" value={subject} onChange={setSubject} error={problems.byField.invoice_email_subject} autoComplete="off" hint="One line, for example: Faktura {invoice_number} från {organization}" />
          <TextAreaField label="Message" name="invoice_email_message" value={message} onChange={setMessage} rows={8} error={problems.byField.invoice_email_message} />
          <details className="text-sm">
            <summary className="cursor-pointer select-none underline">Words you can use</summary>
            <ul className="mt-2 flex flex-col gap-1" data-testid="placeholder-help">
              {PLACEHOLDERS.map(([word, meaning]) => (
                <li key={word}>
                  <code className="rounded bg-surface-hover px-1">{word}</code> {meaning}
                </li>
              ))}
            </ul>
          </details>
          <ErrorSummary messages={error !== null && error.kind !== "validation" ? [error.message] : problems.general} />
          {notice === "saved" && <Notice testId="invoice-email-saved">Saved.</Notice>}
          {notice === "unchanged" && <Notice testId="invoice-email-unchanged">No changes to save.</Notice>}
          <div>
            <Button type="submit" disabled={pending} data-testid="save-invoice-email">
              {pending ? "Saving…" : "Save invoice email text"}
            </Button>
          </div>
        </form>
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          <dt className="font-medium">Subject</dt>
          <dd data-testid="setting-invoice_email_subject">{record.invoice_email_subject ?? <span className="text-zinc-500">Not set</span>}</dd>
          <dt className="font-medium">Message</dt>
          <dd className="whitespace-pre-wrap" data-testid="setting-invoice_email_message">
            {record.invoice_email_message ?? <span className="text-zinc-500">Not set</span>}
          </dd>
        </dl>
      )}
    </section>
  );
}
