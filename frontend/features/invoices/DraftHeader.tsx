"use client";

import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { FieldShell } from "@/components/ui/Field";
import { Notice } from "@/components/ui/Notice";
import { classify, TEXT } from "@/features/invoices/failures";
import { useInvoice, useRegisterEditor } from "@/features/invoices/invoice-context";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { Invoice, InvoiceUpdate } from "@/lib/api/types";
import { isDateShape } from "@/lib/dates";
import { blankToNull, NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = ["invoice_date", "due_date", "description"] as const;
const NOT_A_DATE = "Enter a date such as 2026-10-03.";
const INPUT = "rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900 aria-[invalid=true]:border-red-600";

/**
 * Editing a draft's header: invoice date, due date and description, and nothing else. Source
 * transactions and every amount are not editable (to choose other transactions, delete the draft
 * and create another). Only a draft the user may change gets an "Edit details" button; FastAPI
 * refuses everything else regardless.
 */
export function DraftHeader() {
  const { invoice, editable, busy } = useInvoice();
  const [editing, setEditing] = useState(false);

  // If the invoice stops being an editable draft (issued elsewhere), an open editor must close and
  // must not come back to life later: adjust the state when that changes.
  const [wasEditable, setWasEditable] = useState(editable);
  if (editable !== wasEditable) {
    setWasEditable(editable);
    if (!editable) setEditing(false);
  }

  if (!editable) return null;
  return (
    <section aria-label="Edit details" data-testid="draft-header" className="flex flex-col gap-3">
      {editing ? (
        <HeaderForm key={invoice.id} onClose={() => setEditing(false)} />
      ) : (
        <div>
          <Button type="button" disabled={busy} onClick={() => setEditing(true)} data-testid="edit-details">
            Edit details
          </Button>
        </div>
      )}
    </section>
  );
}

function HeaderForm({ onClose }: { onClose: () => void }) {
  useRegisterEditor();
  const orgId = useOrgId();
  const { invoice, busy, mutate, report, refresh, announce } = useInvoice();

  // What this edit is based on, fixed when it was opened. The server data may move on while the user
  // types (another tab, a refresh); the edit is judged against THIS, never the newest.
  const [base] = useState(() => ({
    version: invoice.version,
    invoiceDate: invoice.invoice_date,
    dueDate: invoice.due_date ?? "",
    description: invoice.description ?? "",
  }));
  const [invoiceDate, setInvoiceDate] = useState(base.invoiceDate);
  const [dueDate, setDueDate] = useState(base.dueDate);
  const [description, setDescription] = useState(base.description);
  const [local, setLocal] = useState<FieldErrors>({});
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [refused, setRefused] = useState(false);
  const [saving, setSaving] = useState(false);

  const changedElsewhere = refused || invoice.version !== base.version;
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];

  async function save() {
    setLocal({});
    setProblems(NO_PROBLEMS);
    const body: InvoiceUpdate = {};
    const errors: FieldErrors = {};
    if (invoiceDate !== base.invoiceDate) {
      if (isDateShape(invoiceDate)) body.invoice_date = invoiceDate;
      else errors.invoice_date = [NOT_A_DATE];
    }
    if (dueDate !== base.dueDate) {
      if (dueDate === "") body.due_date = null;
      else if (isDateShape(dueDate)) body.due_date = dueDate;
      else errors.due_date = [NOT_A_DATE];
    }
    if (blankToNull(description) !== blankToNull(base.description)) body.description = blankToNull(description);
    if (Object.keys(errors).length > 0) {
      setLocal(errors);
      return;
    }
    // Nothing changed: no request, so no version is moved just to have saved.
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    const result = await mutate(() => apiFetch<Invoice>(orgId, `/invoices/${invoice.id}`, { method: "PATCH", body, ifMatch: base.version }));
    setSaving(false);
    if (result === null) return;
    if (result.ok) return onClose();

    const failure = classify(result.error);
    if (failure.kind === "validation") setProblems(problemsFrom(result.error, CONTROLS));
    else if (failure.kind === "stale") {
      // Keep the draft (this form holds its own state; a refresh only updates what is shown around
      // it) and load what is on the server now, so the notice can show it. Never retried silently.
      setRefused(true);
      announce(null);
      refresh();
    } else report(failure);
  }

  return (
    <form
      aria-label="Edit invoice details"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        if (!changedElsewhere) void save();
      }}
      className="flex max-w-xl flex-col gap-4"
    >
      {changedElsewhere && (
        <Notice tone="error" testId="header-conflict">
          <p>{TEXT.headerChanged}</p>
          <p className="mt-1">
            Now on the server: invoice date {invoice.invoice_date}, due date {invoice.due_date ?? "none"}, description {invoice.description ?? "none"}.
          </p>
          <Button
            type="button"
            className="mt-2"
            onClick={() => {
              refresh();
              onClose();
            }}
            data-testid="discard-details"
          >
            Discard my edits and load the latest
          </Button>
        </Notice>
      )}
      <FieldShell label="Invoice date" name="invoice_date" error={errorsFor("invoice_date")}>
        {(control) => <input type="date" {...control} value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} className={INPUT} />}
      </FieldShell>
      <FieldShell label="Due date" name="due_date" error={errorsFor("due_date")}>
        {(control) => <input type="date" {...control} value={dueDate} onChange={(event) => setDueDate(event.target.value)} className={INPUT} />}
      </FieldShell>
      <FieldShell label="Description" name="description" error={errorsFor("description")}>
        {(control) => <textarea rows={3} {...control} value={description} onChange={(event) => setDescription(event.target.value)} className={INPUT} />}
      </FieldShell>
      <ErrorSummary messages={problems.general} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy || changedElsewhere} data-testid="save-details">
          {saving ? "Saving…" : "Save details"}
        </Button>
        <Button type="button" disabled={saving} onClick={onClose} data-testid="cancel-details">
          Cancel
        </Button>
      </div>
    </form>
  );
}
