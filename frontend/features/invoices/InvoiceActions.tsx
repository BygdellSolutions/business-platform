"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { classify } from "@/features/invoices/failures";
import { useInvoice } from "@/features/invoices/invoice-context";
import { apiFetch } from "@/lib/api/client";
import type { Invoice } from "@/lib/api/types";

/**
 * Issue and Delete draft. They appear only for a draft the user's role may change (presentation:
 * FastAPI refuses everyone else), never on an issued invoice, and they wait while an editor is open,
 * a change is running, or an earlier request's outcome is still unknown.
 *
 * Both carry the invoice's current version (If-Match). A refused or unknown outcome is explained and
 * checked, never retried with a newer version on the user's behalf.
 */
export function InvoiceActions() {
  const orgId = useOrgId();
  const router = useRouter();
  const { invoice, canMutate, editable, busy, unverified, editorsOpen, mutate, report, announce, verify, isMounted } = useInvoice();
  const [running, setRunning] = useState<"issue" | "delete" | null>(null);
  const blocked = busy || unverified || editorsOpen > 0;

  async function issue() {
    setRunning("issue");
    const result = await mutate(() => apiFetch<Invoice>(orgId, `/invoices/${invoice.id}/issue`, { method: "POST", ifMatch: invoice.version }));
    setRunning(null);
    if (result === null) return;
    if (result.ok) announce({ tone: "info", text: "Invoice issued." });
    else report(classify(result.error));
  }

  async function remove() {
    setRunning("delete");
    const result = await mutate(() => apiFetch<unknown>(orgId, `/invoices/${invoice.id}`, { method: "DELETE", ifMatch: invoice.version }), { refresh: false });
    setRunning(null);
    if (result === null) return;
    if (result.ok) {
      // Only if this screen is still the one the user is on (not left, not another organization).
      if (!isMounted()) return;
      router.push(`/o/${orgId}/invoices?deleted=1`);
      router.refresh(); // drop cached pages so Back does not show the deleted draft
      return;
    }
    report(classify(result.error));
  }

  if (invoice.status === "issued") return null;
  if (!canMutate) {
    return (
      <p data-testid="no-mutation" className="text-sm text-zinc-600 dark:text-zinc-400">
        You can read this invoice. Only owners, admins and accountants can issue or delete a draft.
      </p>
    );
  }
  if (!editable) return null;
  return (
    <section aria-label="Actions" data-testid="invoice-actions" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <ConfirmButton
          label={running === "issue" ? "Issuing…" : "Issue invoice"}
          question="Issue this invoice? It gets its invoice number now and, once issued, cannot be edited or deleted."
          confirmLabel="Yes, issue it"
          disabled={blocked}
          onConfirm={() => void issue()}
          testId="issue"
        />
        <ConfirmButton
          label={running === "delete" ? "Deleting…" : "Delete draft"}
          question="Delete this draft? Its transactions become invoiceable again."
          confirmLabel="Yes, delete it"
          disabled={blocked}
          onConfirm={() => void remove()}
          testId="delete-draft"
        />
      </div>
      {editorsOpen > 0 && (
        <p data-testid="actions-hint" className="text-sm text-zinc-500">
          Save or cancel your open edit first.
        </p>
      )}
      {unverified && (
        <p data-testid="unverified-hint" className="flex flex-wrap items-center gap-2 text-sm text-zinc-600 dark:text-zinc-400">
          We could not confirm the last request, so no new attempt is offered yet.
          <Button type="button" disabled={busy} onClick={() => void verify()} data-testid="check-again">
            Check again
          </Button>
        </p>
      )}
    </section>
  );
}
