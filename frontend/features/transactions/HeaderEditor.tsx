"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Notice } from "@/components/ui/Notice";
import { customerEntity, customerSearch } from "@/features/customers/customer-picker";
import { useEditor, useRegisterEditor } from "@/features/transactions/editor-context";
import { classify, TEXT } from "@/features/transactions/failures";
import { apiFetch } from "@/lib/api/client";
import type { FieldErrors } from "@/lib/api/errors";
import type { Transaction, TransactionHeaderUpdate } from "@/lib/api/types";
import { isDateShape } from "@/lib/dates";
import { NO_PROBLEMS, problemsFrom, type Problems } from "@/lib/forms";

const CONTROLS = ["billing_customer_id", "transaction_date"] as const;
const NOT_A_DATE = "Enter a date such as 2026-10-03.";

/** Billing customer and date. Editable only while the server says the transaction is a draft. */
export function HeaderEditor() {
  const orgId = useOrgId();
  const { transaction, readOnly, busy, refreshing } = useEditor();
  const [editing, setEditing] = useState(false);

  // If the transaction stops being a draft (completed elsewhere, say), an open editor must not
  // come back to life when it is reopened later: adjust the state when that prop changes.
  const [wasReadOnly, setWasReadOnly] = useState(readOnly);
  if (readOnly !== wasReadOnly) {
    setWasReadOnly(readOnly);
    if (readOnly) setEditing(false);
  }

  return (
    <section aria-label="Details" aria-busy={refreshing} data-testid="header" data-updating={refreshing || undefined} className={`flex flex-col gap-3 ${refreshing ? "opacity-50" : ""}`}>
      <h2 className="text-lg font-medium">Details</h2>
      {editing && !readOnly ? (
        <HeaderForm onClose={() => setEditing(false)} />
      ) : (
        <>
          <dl className="grid max-w-md grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            <dt>Billing customer</dt>
            <dd data-testid="header-customer">
              <Link href={`/o/${orgId}/customers/${transaction.billing_customer_id}`} className="underline">
                {transaction.billing_customer.name}
              </Link>
              {!transaction.billing_customer.active && <span className="ml-1 text-xs text-zinc-500">(inactive)</span>}
            </dd>
            <dt>Date</dt>
            <dd data-testid="header-date">{transaction.transaction_date}</dd>
          </dl>
          {!readOnly && (
            <div>
              <Button type="button" disabled={busy} onClick={() => setEditing(true)} data-testid="edit-header">
                Edit header
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function HeaderForm({ onClose }: { onClose: () => void }) {
  useRegisterEditor();
  const orgId = useOrgId();
  const { transaction, busy, mutate, report, refresh } = useEditor();

  // What this edit is based on, fixed when it was opened. The server data may move on while the
  // user types (another tab, a refresh); the edit is judged against THIS, never the newest.
  const [base] = useState(() => ({
    customerId: transaction.billing_customer_id,
    date: transaction.transaction_date,
    headerVersion: transaction.header_version,
  }));
  const [customer, setCustomer] = useState<PickerEntity | null>(() => customerEntity(transaction.billing_customer));
  const [date, setDate] = useState(base.date);
  const [local, setLocal] = useState<FieldErrors>({});
  const [problems, setProblems] = useState<Problems>(NO_PROBLEMS);
  const [refused, setRefused] = useState(false);
  const [saving, setSaving] = useState(false);

  const search = useMemo(() => customerSearch(orgId, { activeOnly: true }), [orgId]);
  const changedElsewhere = refused || transaction.header_version !== base.headerVersion;
  const errorsFor = (name: string) => local[name] ?? problems.byField[name];

  async function save() {
    setLocal({});
    setProblems(NO_PROBLEMS);
    const body: TransactionHeaderUpdate = {};
    if (customer && customer.id !== base.customerId) body.billing_customer_id = customer.id;
    if (date !== base.date) {
      if (!isDateShape(date)) {
        setLocal({ transaction_date: [NOT_A_DATE] });
        return;
      }
      body.transaction_date = date;
    }
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    const result = await mutate(() => apiFetch<Transaction>(orgId, `/transactions/${transaction.id}`, { method: "PATCH", body, ifMatch: base.headerVersion }));
    setSaving(false);
    if (result === null) return;
    if (result.ok) return onClose();

    const failure = classify(result.error);
    if (failure.kind === "validation") setProblems(problemsFrom(result.error, CONTROLS));
    else if (failure.kind === "stale") {
      // Keep the draft (this editor holds its own state; a refresh only updates what the page
      // shows around it) and load what is on the server now, so the notice can show it.
      setRefused(true);
      refresh();
    }
    else report(failure, "save");
  }

  return (
    <form
      aria-label="Edit header"
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
          <p className="mt-1">Now on the server: {transaction.billing_customer.name}, {transaction.transaction_date}.</p>
          <Button
            type="button"
            className="mt-2"
            onClick={() => {
              refresh();
              onClose();
            }}
            data-testid="discard-header"
          >
            Discard my edits and load the latest
          </Button>
        </Notice>
      )}
      <EntityPicker label="Billing customer" name="billing_customer_id" value={customer} onChange={setCustomer} search={search} error={errorsFor("billing_customer_id")} />
      <label className="flex flex-col gap-1 text-sm font-medium">
        Date
        <input
          type="date"
          name="transaction_date"
          value={date}
          onChange={(event) => setDate(event.target.value)}
          aria-invalid={!!errorsFor("transaction_date")}
          className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900 aria-[invalid=true]:border-red-600"
        />
        {errorsFor("transaction_date") && (
          <span data-testid="error-transaction_date" className="text-sm font-normal text-red-700 dark:text-red-300">
            {errorsFor("transaction_date")!.join(" ")}
          </span>
        )}
      </label>
      <ErrorSummary messages={problems.general} />
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={busy || changedElsewhere} data-testid="save-header">
          {saving ? "Saving…" : "Save header"}
        </Button>
        <Button type="button" disabled={saving} onClick={onClose} data-testid="cancel-header">
          Cancel
        </Button>
      </div>
    </form>
  );
}
