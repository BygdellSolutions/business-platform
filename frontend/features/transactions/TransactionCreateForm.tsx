"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, type FormEvent } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { EntityPicker, type PickerEntity } from "@/components/ui/EntityPicker";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { customerSearch } from "@/features/customers/customer-picker";
import { apiFetch } from "@/lib/api/client";
import type { Transaction, TransactionCreate } from "@/lib/api/types";
import { isDateShape } from "@/lib/dates";
import { problemsFrom, useMutation } from "@/lib/forms";

const CONTROLS = ["billing_customer_id", "transaction_date"] as const;

/**
 * A new transaction starts as a draft with only a billing customer and a date; lines are added
 * on the transaction's own page. Only active customers can be billed (the picker offers only
 * those, and FastAPI refuses others). The date is a plain YYYY-MM-DD string, prefilled with the
 * organization's own date (`today`, from FastAPI in the organization's time zone), not the browser's. No customer chosen means the field is left out and FastAPI says it is
 * required, on the picker.
 */
export function TransactionCreateForm({ today }: { today: string }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [customer, setCustomer] = useState<PickerEntity | null>(null);
  const [typedDate, setTypedDate] = useState<string | null>(null);
  const date = typedDate ?? today;

  const search = useMemo(() => customerSearch(orgId, { activeOnly: true }), [orgId]);
  const problems = problemsFrom(error, CONTROLS);
  const dateError = problems.byField.transaction_date ?? (date !== "" && !isDateShape(date) ? ["Enter a date such as 2026-10-03."] : undefined);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const body: TransactionCreate = {
      ...(customer ? { billing_customer_id: customer.id } : {}),
      ...(date !== "" ? { transaction_date: date } : {}),
    };
    const created = await run(() => apiFetch<Transaction>(orgId, "/transactions", { method: "POST", body }));
    if (created === null) return;
    router.push(`/o/${orgId}/transactions/${created.id}?created=1`);
    router.refresh(); // drop cached pages (a list visited before) so Back does not show them without the new transaction
  }

  return (
    <form onSubmit={onSubmit} noValidate aria-label="New transaction" className="flex max-w-xl flex-col gap-4">
      <EntityPicker label="Billing customer" name="billing_customer_id" value={customer} onChange={setCustomer} search={search} error={problems.byField.billing_customer_id} />
      <label className="flex flex-col gap-1 text-sm font-medium">
        Date
        <input
          type="date"
          name="transaction_date"
          value={date}
          onChange={(event) => setTypedDate(event.target.value)}
          aria-invalid={!!dateError}
          className="rounded border border-zinc-400 px-2 py-1 font-normal dark:bg-zinc-900 aria-[invalid=true]:border-red-600"
        />
        {dateError && (
          <span data-testid="error-transaction_date" className="text-sm font-normal text-red-700 dark:text-red-300">
            {dateError.join(" ")}
          </span>
        )}
      </label>
      <ErrorSummary messages={problems.general} />
      <div className="flex items-center gap-4">
        <Button type="submit" disabled={pending || (date !== "" && !isDateShape(date))} data-testid="submit">
          {pending ? "Creating…" : "Create transaction"}
        </Button>
        <Link href={`/o/${orgId}/transactions`} className="text-sm underline">
          Cancel
        </Link>
      </div>
    </form>
  );
}
