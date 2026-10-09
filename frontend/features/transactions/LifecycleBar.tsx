"use client";

import { useState } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Button } from "@/components/ui/Button";
import { ConfirmButton } from "@/components/ui/ConfirmButton";
import { useEditor } from "@/features/transactions/editor-context";
import { classify } from "@/features/transactions/failures";
import { apiFetch } from "@/lib/api/client";
import type { Transaction } from "@/lib/api/types";

type Action = "complete" | "reopen" | "cancel";

const PROGRESS: Record<Action, string> = { complete: "Completing…", reopen: "Reopening…", cancel: "Cancelling…" };

/**
 * Complete, reopen, cancel. Which buttons appear is presentation (they follow the status the
 * server last sent); whether a step is allowed is FastAPI's decision, and a refused step is
 * explained and followed by a refresh. Each step carries the transaction's `version`, so a step
 * decided on a stale screen (a line was added elsewhere, say) is refused instead of applied.
 *
 * The buttons wait while any editor is open: completing would otherwise silently leave the
 * user's unsaved edits behind.
 */
export function LifecycleBar() {
  const orgId = useOrgId();
  const { transaction, busy, editorsOpen, mutate, report, announce } = useEditor();
  const [running, setRunning] = useState<Action | null>(null);
  const blocked = busy || editorsOpen > 0;

  async function act(action: Action) {
    setRunning(action);
    const result = await mutate(() =>
      apiFetch<Transaction>(orgId, `/transactions/${transaction.id}/${action}`, { method: "POST", ifMatch: transaction.version }),
    );
    setRunning(null);
    if (result === null) return;
    if (!result.ok) {
      const failure = classify(result.error);
      if (failure.kind === "problems") announce(null); // the problems replace any earlier message below
      report(failure, "lifecycle");
    }
  }

  const { status } = transaction;
  return (
    <section aria-label="Actions" data-testid="lifecycle" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3">
        {status === "draft" && (
          <Button type="button" disabled={blocked} onClick={() => void act("complete")} data-testid="complete">
            {running === "complete" ? PROGRESS.complete : "Complete"}
          </Button>
        )}
        {status === "completed" && (
          <Button type="button" disabled={blocked} onClick={() => void act("reopen")} data-testid="reopen">
            {running === "reopen" ? PROGRESS.reopen : "Reopen"}
          </Button>
        )}
        {(status === "draft" || status === "completed") && (
          <ConfirmButton
            label={running === "cancel" ? PROGRESS.cancel : "Cancel order"}
            question="Cancel this order? This is final."
            confirmLabel="Yes, cancel it"
            disabled={blocked}
            onConfirm={() => void act("cancel")}
            testId="cancel"
          />
        )}
        {status === "cancelled" && <span className="text-sm text-zinc-500">No further actions.</span>}
      </div>
      {editorsOpen > 0 && status !== "cancelled" && (
        <p data-testid="lifecycle-hint" className="text-sm text-zinc-500">
          Save or cancel your open edit first.
        </p>
      )}
    </section>
  );
}
