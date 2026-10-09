"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { TextAreaField } from "@/components/ui/Field";
import { DownloadPdf } from "@/features/invoices/DownloadPdf";
import { apiFetch } from "@/lib/api/client";
import type { CreditNoteSummary, Invoice, InvoiceLine, InvoiceReturn } from "@/lib/api/types";
import { trimQuantity } from "@/lib/decimal";
import { problemsFrom, useMutation } from "@/lib/forms";
import { formatTimestamp } from "@/lib/timestamps";

const CELL = "rounded border border-zinc-400 px-2 py-1 text-right dark:bg-zinc-900";
const ZERO = /^0*(?:\.0*)?$/;

/** Shape only, no arithmetic: is there anything (left) in this quantity string? */
function nothing(value: string | null): boolean {
  return value === null || ZERO.test(value.trim());
}

interface Row {
  quantity: string;
  returned: boolean;
}

/** The case's lines as the form's rows. Goods that went back into stock at "goods received" are not returned again. */
function fromReturn(item: InvoiceReturn): Record<string, Row> {
  return Object.fromEntries(item.lines.map((line) => [line.invoice_line_id, { quantity: line.quantity, returned: false }]));
}

/**
 * Credit notes (kreditfakturor) of an issued invoice: the ones made so far, each with its PDF, and for those who keep the
 * books a form that credits quantities of the invoice's own lines. "Credit all" fills in everything that is left. A
 * line whose goods Inventory can take back offers "Returned to stock". The backend works out every amount, refuses more
 * than is left of a line, and numbers the credit note from the invoice's series; nothing can be changed afterwards.
 */
export function CreditNotesPanel({
  invoice,
  canCredit,
  timeZone,
  creditReturnId,
}: {
  invoice: Invoice;
  canCredit: boolean;
  timeZone: string | null;
  /** An approved return case to credit: the form opens with its lines and reason, and the credit note closes it. */
  creditReturnId?: string;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const approved = invoice.returns.filter((item) => item.state === "approved");
  const start = approved.find((item) => item.id === creditReturnId);
  const [returnId, setReturnId] = useState<string | null>(start?.id ?? null);
  const [open, setOpen] = useState(start !== undefined);
  const [reason, setReason] = useState(start ? `Return: ${start.reason}` : "");
  const [rows, setRows] = useState<Record<string, Row>>(start ? fromReturn(start) : {});
  const [created, setCreated] = useState<string | null>(null);

  if (invoice.status !== "issued") return null;
  const creditable = invoice.lines.filter((line) => !nothing(line.creditable_quantity));
  const submitted = creditable.filter((line) => !nothing(rows[line.id]?.quantity ?? ""));
  const controls = ["reason", ...submitted.flatMap((_, index) => [`lines.${index}.quantity`, `lines.${index}.returned_to_stock`, `lines.${index}.invoice_line_id`])];
  const problems = problemsFrom(error, controls);
  const lineError = (line: InvoiceLine) => {
    const index = submitted.indexOf(line);
    if (index < 0) return null;
    const messages = [`quantity`, `returned_to_stock`, `invoice_line_id`].flatMap((key) => problems.byField[`lines.${index}.${key}`] ?? []);
    return messages.length > 0 ? messages.join(" ") : null;
  };
  const row = (line: InvoiceLine): Row => rows[line.id] ?? { quantity: "", returned: false };
  const set = (line: InvoiceLine, change: Partial<Row>) => setRows((current) => ({ ...current, [line.id]: { ...row(line), ...change } }));

  function creditReturn(item: InvoiceReturn) {
    setReturnId(item.id);
    setReason(`Return: ${item.reason}`);
    setRows(fromReturn(item));
    setOpen(true);
  }

  function creditAll() {
    setRows(Object.fromEntries(creditable.map((line) => [line.id, { quantity: line.creditable_quantity ?? "", returned: row(line).returned }])));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const body = {
      reason: reason.trim(),
      lines: submitted.map((line) => ({ invoice_line_id: line.id, quantity: row(line).quantity.trim(), returned_to_stock: row(line).returned })),
      ...(returnId ? { return_id: returnId } : {}),
    };
    const note = await run(() => apiFetch<CreditNoteSummary>(orgId, `/invoices/${invoice.id}/credit-notes`, { method: "POST", body }));
    if (note === null) return;
    setCreated(note.number_text);
    setReturnId(null);
    setOpen(false);
    setRows({});
    setReason("");
    router.refresh();
  }

  if (invoice.credit_notes.length === 0 && !canCredit) return null;

  return (
    <section id="credit-notes" aria-label="Credit notes" data-testid="credit-notes" className="flex max-w-4xl flex-col gap-3">
      <h2 className="text-lg font-semibold">Credit notes</h2>
      {created && (
        <p role="status" data-testid="credit-created" className="text-sm text-green-800 dark:text-green-300">
          Credit note {created} created.
        </p>
      )}
      {invoice.credit_notes.length === 0 ? (
        <p className="text-sm text-zinc-500" data-testid="no-credit-notes">
          None. Credit lines here when goods come back or a charge was wrong; the invoice itself never changes.
        </p>
      ) : (
        <table className="text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-300 dark:border-zinc-700">
              <th className="py-1 pr-4">Number</th>
              <th className="py-1 pr-4">Date</th>
              <th className="py-1 pr-4">Reason</th>
              <th className="py-1 pr-4 text-right">Credited</th>
              <th className="py-1 pr-4">Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {invoice.credit_notes.map((note) => (
              <tr key={note.id} data-testid="credit-note-row" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                <td className="py-1 pr-4" data-testid="credit-note-number">
                  {note.number_text}
                </td>
                <td className="py-1 pr-4">{note.credit_date}</td>
                <td className="py-1 pr-4">{note.reason}</td>
                <td className="py-1 pr-4 text-right" data-testid="credit-note-gross">
                  <DecimalText value={note.gross_amount} /> {note.currency}
                </td>
                <td className="py-1 pr-4 text-xs text-zinc-500">
                  {formatTimestamp(note.issued_at, timeZone)}
                  {note.issued_by_name && ` by ${note.issued_by_name}`}
                </td>
                <td className="py-1">
                  <DownloadPdf invoice={{ id: note.id, status: "issued" }} path={`/invoices/credit-notes/${note.id}/pdf`} label="PDF" />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {canCredit && creditable.length > 0 && !open && (
        <div className="flex flex-wrap gap-3">
          {approved.map((item) => (
            <Button key={item.id} type="button" onClick={() => creditReturn(item)} data-testid="credit-return">
              Credit the approved return ({item.reason})
            </Button>
          ))}
          <Button type="button" onClick={() => { setReturnId(null); setOpen(true); }} data-testid="open-credit">
            Credit…
          </Button>
        </div>
      )}
      {canCredit && creditable.length === 0 && invoice.credit_notes.length > 0 && <p className="text-sm text-zinc-500">Everything on this invoice is credited.</p>}

      {canCredit && open && creditable.length > 0 && (
        <form onSubmit={submit} noValidate aria-label="Credit lines" className="flex flex-col gap-3 rounded border border-zinc-300 p-3 dark:border-zinc-700">
          <div className="flex items-center justify-between gap-3">
            <h3 className="font-medium">{returnId ? "Credit note for the approved return" : "New credit note"}</h3>
            <Button type="button" onClick={creditAll} data-testid="credit-all">
              Credit all
            </Button>
          </div>
          <table className="text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-300 dark:border-zinc-700">
                <th className="py-1 pr-4">Line</th>
                <th className="py-1 pr-4">Unit</th>
                <th className="py-1 pr-4 text-right">Invoiced</th>
                <th className="py-1 pr-4 text-right">Credited</th>
                <th className="py-1 pr-4 text-right">Unit price</th>
                <th className="py-1 pr-4 text-right">Credit</th>
                <th className="py-1 pr-4">Returned to stock</th>
              </tr>
            </thead>
            <tbody>
              {creditable.map((line) => {
                const problem = lineError(line);
                const canReturn = !nothing(line.stock_returnable);
                return (
                  <tr key={line.id} data-testid="credit-line" className="border-b border-zinc-200 align-top dark:border-zinc-800">
                    <td className="py-1 pr-4">{line.description}</td>
                    <td className="py-1 pr-4">{line.unit}</td>
                    <td className="py-1 pr-4 text-right">{trimQuantity(line.quantity)}</td>
                    <td className="py-1 pr-4 text-right">{trimQuantity(line.credited_quantity)}</td>
                    <td className="py-1 pr-4 text-right">
                      <DecimalText value={line.unit_price_ex_vat} />
                    </td>
                    <td className="py-1 pr-4 text-right">
                      <input
                        type="text"
                        inputMode="decimal"
                        aria-label={`Credit quantity of ${line.description}`}
                        placeholder={`max ${trimQuantity(line.creditable_quantity ?? "")}`}
                        value={row(line).quantity}
                        onChange={(event) => set(line, { quantity: event.target.value })}
                        className={`${CELL} w-28`}
                        data-testid="credit-quantity"
                        aria-invalid={problem !== null}
                      />
                      {problem && (
                        <span className="block max-w-xs text-left text-xs text-red-700 dark:text-red-300" data-testid="credit-line-error">
                          {problem}
                        </span>
                      )}
                    </td>
                    <td className="py-1 pr-4">
                      {canReturn ? (
                        <label className="flex items-center gap-2">
                          <input
                            type="checkbox"
                            checked={row(line).returned}
                            onChange={(event) => set(line, { returned: event.target.checked })}
                            data-testid="credit-returned"
                            aria-label={`${line.description} returned to stock`}
                          />
                          <span className="text-xs text-zinc-500">up to {trimQuantity(line.stock_returnable ?? "")}</span>
                        </label>
                      ) : (
                        <span className="text-xs text-zinc-500">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <TextAreaField label="Reason" name="reason" value={reason} onChange={setReason} error={problems.byField.reason} required rows={2} />
          <p className="text-xs text-zinc-500">The amounts are the invoice&apos;s own prices; the credit note gets the next number and cannot be changed afterwards.</p>
          <ErrorSummary messages={problems.general} />
          <div className="flex gap-3">
            <Button type="submit" disabled={pending} data-testid="create-credit-note">
              {pending ? "Creating…" : "Create credit note"}
            </Button>
            <Button type="button" onClick={() => setOpen(false)} disabled={pending} className="bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
              Cancel
            </Button>
          </div>
        </form>
      )}
    </section>
  );
}
