"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { ErrorSummary } from "@/components/ui/ErrorSummary";
import { Notice } from "@/components/ui/Notice";
import { apiFetch } from "@/lib/api/client";
import type { AssignCurrencyResult, CurrencyStatus } from "@/lib/api/types";
import { problemsFrom, useMutation } from "@/lib/forms";

/**
 * Transactions that were created before currencies existed have no currency, and the platform
 * never guesses one. An owner or admin can state that they were all priced in the organization's
 * currency; until then they stay as they are (and are shown as having no currency). Nothing
 * appears here when every transaction already has a currency.
 */
export function EarlierTransactions({ status, canEdit }: { status: CurrencyStatus; canEdit: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const { pending, error, run } = useMutation();
  const [assigned, setAssigned] = useState<AssignCurrencyResult | null>(null);
  const problems = problemsFrom(error, []);

  if (status.transactions_without_currency === 0 && assigned === null) return null;
  const count = status.transactions_without_currency;
  const noun = count === 1 ? "transaction was" : "transactions were";

  async function assign(currency: string) {
    const result = await run(() => apiFetch<AssignCurrencyResult>(orgId, "/transactions/assign-currency", { method: "POST", body: { currency } }));
    if (result === null) return;
    setAssigned(result);
    router.refresh();
  }

  return (
    <section aria-label="Earlier transactions" data-testid="earlier-transactions" className="flex max-w-xl flex-col gap-2">
      <h2 className="text-lg font-medium">Earlier transactions</h2>
      {assigned !== null ? (
        <Notice testId="assigned">
          {assigned.assigned} {assigned.assigned === 1 ? "transaction now has" : "transactions now have"} the currency {assigned.currency}.
        </Notice>
      ) : (
        <>
          <p className="text-sm" data-testid="earlier-count">
            {count} {noun} created before currencies existed and {count === 1 ? "has" : "have"} no currency.
          </p>
          {status.default_currency === null ? (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">Set the organization&apos;s default currency first; then you can state that these were priced in it.</p>
          ) : canEdit ? (
            <>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                If their prices were in {status.default_currency}, assign it to them. This is a statement about your own records, so it is never done automatically.
              </p>
              <div>
                <ConfirmButton
                  label={`Assign ${status.default_currency} to these transactions`}
                  question={`Assign ${status.default_currency} to ${count} ${count === 1 ? "transaction" : "transactions"}? This cannot be undone.`}
                  confirmLabel="Assign"
                  onConfirm={() => void assign(status.default_currency as string)}
                  disabled={pending}
                  testId="assign-currency"
                />
              </div>
            </>
          ) : (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">An owner or admin can assign the organization&apos;s currency to them.</p>
          )}
        </>
      )}
      <ErrorSummary messages={problems.general} />
    </section>
  );
}
