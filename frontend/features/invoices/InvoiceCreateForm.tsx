"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useRef, useState, useTransition, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { DecimalText } from "@/components/ui/DecimalText";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FieldShell } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { compatibility, createBody, REASON, toggle, without } from "@/features/invoices/eligibility";
import { classify } from "@/features/invoices/failures";
import { apiFetch } from "@/lib/api/client";
import type { Invoice, Invoiceable } from "@/lib/api/types";
import { isDateShape } from "@/lib/dates";
import { useMutation } from "@/lib/forms";

const INPUT = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900 aria-[invalid=true]:border-red-600";
const NOT_A_DATE = "Enter a date such as 2026-10-03.";

/**
 * Choose completed transactions and create a draft invoice from them.
 *
 * Eligibility is the server's: `rows` is what GET /api/invoiceable-transactions returned for this
 * page. The selection is the only client state, and it can only hold transactions of ONE billing
 * customer and ONE currency: once something is selected, rows that do not match cannot be
 * selected (and say why). The request carries the chosen ids and the header fields, never a
 * customer or a currency.
 *
 * A transaction can stop being invoiceable between listing and creating. FastAPI then answers a
 * structured conflict; this screen shows it, forgets the transactions it names, and re-reads
 * the eligible list. No combined amount is shown: the backend has no authoritative aggregate for a
 * selection, and the frontend does not add money.
 */
export function InvoiceCreateForm({
  rows,
  hasNext,
  base,
  page,
  customerFilter,
  canMutate = true,
}: {
  rows: Invoiceable[];
  hasNext: boolean;
  base: string;
  page: number;
  customerFilter: string | null;
  /** The user's role may create invoices. Without it the list is shown read-only: no selection, no form. Presentation only; FastAPI decides. */
  canMutate?: boolean;
}) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [refreshing, startRefresh] = useTransition();
  const [selection, setSelection] = useState<Invoiceable[]>([]);
  const [invoiceDate, setInvoiceDate] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [description, setDescription] = useState("");
  const [conflict, setConflict] = useState<{ message: string; lines: string[] } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(() => {
    if (mounted.current) startRefresh(() => router.refresh());
  }, [router]);

  // Eligibility can change while the tab is in the background: re-read it when the tab returns.
  // The selection lives in this component, so a refresh never touches it.
  const onVisible = useEffectEvent(() => {
    if (document.visibilityState === "visible" && !pending) refresh();
  });
  useEffect(() => {
    const listener = () => onVisible();
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  }, []);

  const first = selection[0];
  const dateErrors: string[] = [];
  if (invoiceDate !== "" && !isDateShape(invoiceDate)) dateErrors.push(NOT_A_DATE);
  const dueErrors: string[] = [];
  if (dueDate !== "" && !isDateShape(dueDate)) dueErrors.push(NOT_A_DATE);
  const serverErrors = error?.kind === "validation" ? error.fieldErrors : {};
  // Answers the form does not explain itself (the conflicts and unknown outcomes above have their own notices).
  const general = error?.kind === "validation" ? error.formErrors : error && ["forbidden", "client", "unauthorized"].includes(error.kind) ? [error.message] : [];
  const labelOf = (id: string) => {
    const row = selection.find((selected) => selected.id === id) ?? rows.find((candidate) => candidate.id === id);
    return row ? `${row.transaction_date} · ${row.billing_customer.name}` : "A selected order";
  };

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (selection.length === 0 || dateErrors.length > 0 || dueErrors.length > 0) return;
    setConflict(null);
    setProblem(null);
    const body = createBody(selection, { invoiceDate, dueDate, description });
    const created = await run(async () => {
      const result = await apiFetch<Invoice>(orgId, "/invoices", { method: "POST", body });
      if (!result.ok && mounted.current) {
        const failure = classify(result.error);
        if (failure.kind === "ineligible") {
          // Authoritative: forget what the backend says can no longer be invoiced, and re-read eligibility.
          setConflict({ message: failure.message, lines: failure.transactionIds.map(labelOf) });
          setSelection((current) => (failure.transactionIds.length > 0 ? without(current, failure.transactionIds) : current));
          refresh();
        } else if (failure.kind === "conflict") {
          setProblem(failure.message);
          refresh();
        } else if (failure.kind === "validation" && failure.fieldErrors.transaction_ids) {
          setProblem("One or more of the selected orders could not be found. The eligible list was re-read.");
          refresh();
        } else if (failure.kind === "unconfirmed") {
          // Unknown outcome: the draft may exist. Re-read eligibility (its transactions would be gone from it) and say so.
          setProblem("We could not confirm whether the draft invoice was created. Check the invoice list before trying again.");
          refresh();
        }
      }
      return result;
    });
    if (created === null) return;
    // Only if this screen is still the one the user is on (not left, not another organization).
    if (!mounted.current) return;
    router.push(`/o/${orgId}/invoices/${created.id}?created=1`);
    router.refresh(); // drop cached pages (the lists visited before) so Back does not show them without the new invoice
  }

  return (
    <div className="flex flex-col gap-4">
      {canMutate && (
      <section aria-label="Selection" data-testid="selection" className="flex max-w-3xl flex-col gap-2 rounded border border-zinc-300 p-3 text-sm dark:border-zinc-700">
        {first === undefined ? (
          <p data-testid="selection-empty">Select one or more completed orders. All of them must have the same billing customer and currency.</p>
        ) : (
          <>
            <p data-testid="selection-summary">
              <span className="font-medium">Billing customer:</span> <span data-testid="selection-customer">{first.billing_customer.name}</span> · <span className="font-medium">Currency:</span>{" "}
              <span data-testid="selection-currency">{first.currency}</span> · <span data-testid="selection-count">{selection.length}</span> selected
            </p>
            <ul className="flex flex-col gap-1">
              {selection.map((selected) => (
                <li key={selected.id} data-testid="selected-row" className="flex flex-wrap items-center gap-3">
                  <span>{selected.transaction_date}</span>
                  <span>
                    net <DecimalText value={selected.totals.net_amount} />, VAT <DecimalText value={selected.totals.vat_amount} />, gross <DecimalText value={selected.totals.gross_amount} />
                  </span>
                  <button type="button" className="underline" onClick={() => setSelection((current) => toggle(current, selected))} data-testid="unselect">
                    Remove
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap items-center gap-4">
              <button type="button" className="underline" onClick={() => setSelection([])} data-testid="clear-selection">
                Clear selection
              </button>
              {customerFilter === null && (
                <Link href={`${base}?customer_id=${first.billing_customer_id}`} className="underline" data-testid="only-this-customer">
                  Show only this customer&apos;s orders
                </Link>
              )}
            </div>
          </>
        )}
      </section>
      )}

      {conflict && (
        <Notice tone="error" testId="eligibility-conflict">
          <p>{conflict.message}</p>
          {conflict.lines.length > 0 && (
            <ul className="mt-1 list-disc pl-5" data-testid="conflict-transactions">
              {conflict.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}
          <p className="mt-1">Those were removed from your selection and the list below was refreshed.</p>
        </Notice>
      )}
      {problem && (
        <Notice tone="error" testId="create-problem">
          {problem}
        </Notice>
      )}

      <div aria-busy={refreshing} data-testid="eligible" className={refreshing ? "opacity-50" : ""}>
        {rows.length === 0 ? (
          <p data-testid="empty">{customerFilter !== null ? "No invoiceable orders for this customer." : "No completed orders are waiting to be invoiced."}</p>
        ) : (
          <div className="overflow-x-auto">
            <table data-testid="eligible-table" className="w-full max-w-5xl text-left text-sm">
              <thead>
                <tr className="border-b border-zinc-300 dark:border-zinc-700">
                  {canMutate && (
                    <th className="py-1 pr-3">
                      <span className="sr-only">Select</span>
                    </th>
                  )}
                  <th className="py-1 pr-4">Date</th>
                  <th className="py-1 pr-4">Billing customer</th>
                  <th className="py-1 pr-4">Currency</th>
                  <th className="py-1 pr-4 text-right">Lines</th>
                  <th className="py-1 pr-4 text-right">Net</th>
                  <th className="py-1 pr-4 text-right">VAT</th>
                  <th className="py-1 text-right">Gross</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const chosen = selection.some((selected) => selected.id === row.id);
                  const fit = chosen ? "ok" : compatibility(selection, row);
                  const label = `Select order of ${row.transaction_date}, ${row.billing_customer.name}`;
                  return (
                    <tr key={row.id} data-testid="eligible-row" data-compat={fit} className={`border-b border-zinc-200 dark:border-zinc-800 ${fit === "ok" ? "" : "text-zinc-400"}`}>
                      {canMutate && (
                        <td className="py-1 pr-3">
                          <input type="checkbox" aria-label={label} checked={chosen} disabled={fit !== "ok"} onChange={() => setSelection((current) => toggle(current, row))} data-testid="select-transaction" />
                        </td>
                      )}
                      <td className="py-1 pr-4">
                        <Link href={`/o/${orgId}/transactions/${row.id}`} className="underline">
                          {row.transaction_date}
                        </Link>
                      </td>
                      <td className="py-1 pr-4" data-testid="eligible-customer">
                        {row.billing_customer.name}
                        {canMutate && fit !== "ok" && (
                          <span className="ml-2 text-xs" data-testid="incompatible-reason">
                            {REASON[fit]}
                          </span>
                        )}
                      </td>
                      <td className="py-1 pr-4" data-testid="eligible-currency">
                        {row.currency}
                      </td>
                      <td className="py-1 pr-4 text-right">{row.line_count}</td>
                      <td className="py-1 pr-4 text-right">
                        <DecimalText value={row.totals.net_amount} />
                      </td>
                      <td className="py-1 pr-4 text-right">
                        <DecimalText value={row.totals.vat_amount} />
                      </td>
                      <td className="py-1 text-right">
                        <DecimalText value={row.totals.gross_amount} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <nav aria-label="Pages" className="mt-2 flex gap-4 text-sm">
          {page > 1 && (
            <Link href={pageHref(base, customerFilter, page - 1)} className="underline" data-testid="prev-page">
              Previous
            </Link>
          )}
          {hasNext && (
            <Link href={pageHref(base, customerFilter, page + 1)} className="underline" data-testid="next-page">
              Next
            </Link>
          )}
          {customerFilter !== null && (
            <Link href={base} className="underline" data-testid="clear-customer-filter">
              Show all customers
            </Link>
          )}
        </nav>
      </div>

      {canMutate && (
      <form onSubmit={onSubmit} noValidate aria-label="New invoice" className="flex max-w-xl flex-col gap-4">
        <FieldShell label="Invoice date" name="invoice_date" hint="Leave empty for today." error={[...dateErrors, ...(serverErrors.invoice_date ?? [])]}>
          {(control) => <input type="date" {...control} value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} className={INPUT} />}
        </FieldShell>
        <FieldShell label="Due date" name="due_date" error={[...dueErrors, ...(serverErrors.due_date ?? [])]}>
          {(control) => <input type="date" {...control} value={dueDate} onChange={(event) => setDueDate(event.target.value)} className={INPUT} />}
        </FieldShell>
        <FieldShell label="Description" name="description" error={serverErrors.description}>
          {(control) => <textarea rows={3} {...control} value={description} onChange={(event) => setDescription(event.target.value)} className={INPUT} />}
        </FieldShell>
        <ErrorSummary messages={general} />
        <div className="flex items-center gap-4">
          <Button type="submit" disabled={pending || selection.length === 0 || dateErrors.length > 0 || dueErrors.length > 0} data-testid="submit">
            {pending ? "Creating…" : "Create draft invoice"}
          </Button>
          <Link href={`/o/${orgId}/invoices`} className="text-sm underline">
            Cancel
          </Link>
        </div>
      </form>
      )}
    </div>
  );
}

function pageHref(base: string, customer: string | null, page: number): string {
  const query = new URLSearchParams();
  if (customer !== null) query.set("customer_id", customer);
  if (page > 1) query.set("page", String(page));
  const text = query.toString();
  return text ? `${base}?${text}` : base;
}
