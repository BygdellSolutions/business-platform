"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { DecimalField, SelectField, TextField } from "@/components/ui/Field";
import { PAYMENT_METHODS, PAYMENT_STATES } from "@/features/invoices/payment-labels";
import { apiFetch } from "@/lib/api/client";
import type { Invoice, InvoicePayment } from "@/lib/api/types";
import { blankToNull, problemsFrom, useMutation } from "@/lib/forms";
import { formatTimestamp } from "@/lib/timestamps";
import { SortHeader } from "@/components/ui/SortHeader";
import type { SortValue } from "@/lib/table-sort";
import { useSortedRows } from "@/lib/use-sorted-rows";

/** What each sortable column sorts by (display only). */
const SORT_COLUMNS: Record<string, (r: InvoicePayment) => SortValue> = {
  paid_on: (r) => ({ text: r.paid_on }),
  amount: (r) => ({ decimal: r.amount }),
  method: (r) => ({ text: r.method }),
  reference: (r) => ({ text: r.reference }),
  recorded: (r) => ({ text: r.created_at }),
};

const CONTROLS = ["amount", "paid_on", "method", "reference", "note"] as const;
const DATE = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900";

const METHOD_LABELS = Object.fromEntries(PAYMENT_METHODS.map((method) => [method.value, method.label]));

const ZERO = /^0*(?:\.0*)?$/;

/**
 * Payments of an issued invoice, recorded by hand: what is paid and outstanding (after credit notes), every payment,
 * refund and reversal with who recorded it, and, for those who keep the books, a form to record a payment, or a refund
 * when more was paid than is owed after a credit. A row recorded by mistake is reversed, never deleted. The backend
 * refuses more than is outstanding (or due back) and a date in the future.
 */
export function PaymentsPanel({ invoice, canRecord, today, timeZone }: { invoice: Invoice; canRecord: boolean; today: string; timeZone: string | null }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const outstanding = invoice.outstanding_amount ?? "0.00";
  const refundDue = invoice.refund_due_amount ?? "0.00";
  const owesRefund = !ZERO.test(refundDue);
  const [form, setForm] = useState({ amount: owesRefund ? refundDue : outstanding, paid_on: today, method: owesRefund ? "bank_transfer" : "bankgiro", reference: "", note: "" });
  const problems = problemsFrom(error, CONTROLS);
  const set = (key: keyof typeof form) => (value: string) => setForm((current) => ({ ...current, [key]: value }));

  const sorted = useSortedRows(invoice.payments, SORT_COLUMNS);
  if (invoice.status !== "issued" || invoice.payment_status === null) return null;

  async function record(event: FormEvent) {
    event.preventDefault();
    const body = { amount: form.amount.trim(), paid_on: form.paid_on, method: form.method, reference: blankToNull(form.reference), note: blankToNull(form.note) };
    const saved = await run(() => apiFetch<Invoice>(orgId, `/invoices/${invoice.id}/${owesRefund ? "refunds" : "payments"}`, { method: "POST", body }));
    if (saved === null) return;
    setForm({ amount: saved.outstanding_amount ?? "0.00", paid_on: today, method: form.method, reference: "", note: "" });
    router.refresh();
  }

  async function reverse(paymentId: string) {
    const saved = await run(() => apiFetch<Invoice>(orgId, `/invoices/${invoice.id}/payments/${paymentId}/reverse`, { method: "POST", body: {} }));
    if (saved !== null) router.refresh();
  }

  return (
    <section aria-label="Payments" data-testid="payments-panel" className="flex max-w-3xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Payments</h2>
      <p className="text-sm">
        <span className="font-semibold" data-testid="payment-status">
          {PAYMENT_STATES[invoice.payment_status]}
        </span>{" "}
        · paid <DecimalText value={invoice.paid_amount ?? "0.00"} /> · outstanding{" "}
        <span data-testid="payment-outstanding">
          <DecimalText value={outstanding} />
        </span>{" "}
        {invoice.currency}
        {invoice.credited_amount && !ZERO.test(invoice.credited_amount) && (
          <>
            {" "}
            · credited{" "}
            <span data-testid="payment-credited">
              <DecimalText value={invoice.credited_amount} />
            </span>
          </>
        )}
      </p>
      {owesRefund && (
        <p className="text-sm font-semibold text-amber-800 dark:text-amber-300" data-testid="refund-due-amount">
          Refund due: <DecimalText value={refundDue} /> {invoice.currency} was paid beyond what is owed after credit notes.
        </p>
      )}
      {invoice.payments.length > 0 && (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <SortHeader label="Paid on" {...sorted.header("paid_on")} />
              <SortHeader label="Amount" {...sorted.header("amount")} align="right" />
              <SortHeader label="Method" {...sorted.header("method")} />
              <SortHeader label="Reference" {...sorted.header("reference")} />
              <SortHeader label="Recorded" {...sorted.header("recorded")} />
              <th />
            </tr>
          </thead>
          <tbody>
            {sorted.rows.map((payment) => (
              <tr key={payment.id} data-testid="payment-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                <td className="py-1 pr-4">{payment.paid_on}</td>
                <td className="py-1 pr-4 text-right">
                  <DecimalText value={payment.amount} />
                </td>
                <td className="py-1 pr-4">
                  {payment.kind === "reversal" ? "Reversal" : payment.kind === "refund" ? `Refund · ${METHOD_LABELS[payment.method] ?? payment.method}` : METHOD_LABELS[payment.method] ?? payment.method}
                  {payment.reversed && <span className="block text-xs text-zinc-500">reversed</span>}
                </td>
                <td className="py-1 pr-4">
                  {payment.reference}
                  {payment.note && <span className="block text-xs italic text-zinc-500">{payment.note}</span>}
                </td>
                <td className="py-1 pr-4 text-xs text-zinc-500">
                  {formatTimestamp(payment.created_at, timeZone)}
                  {payment.created_by_name && ` by ${payment.created_by_name}`}
                </td>
                <td className="py-1">
                  {canRecord && !payment.reversed && payment.kind !== "reversal" && (
                    <ConfirmButton
                      label="Reverse"
                      question={`Reverse this ${payment.kind}? A reversal is recorded; nothing is deleted.`}
                      confirmLabel="Yes, reverse"
                      disabled={pending}
                      onConfirm={() => void reverse(payment.id)}
                      testId="reverse-payment"
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {canRecord && (invoice.payment_status !== "paid" || owesRefund) && (
        <form onSubmit={record} noValidate aria-label={owesRefund ? "Record refund" : "Record payment"} className="flex flex-col gap-3">
          <h3 className="font-medium">{owesRefund ? "Record a refund (money paid back)" : "Record a payment"}</h3>
          <DecimalField label={`Amount (${invoice.currency})`} name="amount" value={form.amount} onChange={set("amount")} error={problems.byField.amount} />
          <label className="flex flex-col gap-1 text-sm font-medium">
            {owesRefund ? "Paid back on" : "Paid on"}
            <input type="date" name="paid_on" value={form.paid_on} max={today} onChange={(event) => set("paid_on")(event.target.value)} className={DATE} />
            {problems.byField.paid_on && (
              <span className="text-sm font-normal text-red-700 dark:text-red-300" data-testid="error-paid_on">
                {problems.byField.paid_on.join(" ")}
              </span>
            )}
          </label>
          <SelectField label="Method" name="method" value={form.method} onChange={set("method")} options={PAYMENT_METHODS} error={problems.byField.method} />
          <TextField label="Reference (optional)" name="reference" value={form.reference} onChange={set("reference")} error={problems.byField.reference} autoComplete="off" hint="For example the OCR or the bank's reference." />
          <TextField label="Note (optional)" name="note" value={form.note} onChange={set("note")} error={problems.byField.note} autoComplete="off" />
          <ErrorSummary messages={problems.general} />
          <div>
            <Button type="submit" disabled={pending} data-testid={owesRefund ? "record-refund" : "record-payment"}>
              {pending ? "Saving…" : owesRefund ? "Record refund" : "Record payment"}
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
