"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, useTransition } from "react";

import { Notice } from "@/components/ui/Notice";
import { AddLineForm } from "@/features/transactions/AddLineForm";
import { EditorContext, type EditorApi, type EditorNotice } from "@/features/transactions/editor-context";
import { describeProblem, TEXT, type Failure } from "@/features/transactions/failures";
import { HeaderEditor } from "@/features/transactions/HeaderEditor";
import { LifecycleBar } from "@/features/transactions/LifecycleBar";
import { LinesTable } from "@/features/transactions/LinesTable";
import { TotalsPanel } from "@/features/transactions/TotalsPanel";
import { TransactionStatusBadge } from "@/features/transactions/TransactionStatusBadge";
import type { ApiResult } from "@/lib/api/errors";
import type { Transaction } from "@/lib/api/types";

/**
 * The transaction page's interactive part.
 *
 * Source of truth: the `transaction` prop, read on the server by the page. Every successful
 * change is followed by `router.refresh()`, which re-reads the whole transaction from FastAPI,
 * so lines, amounts, totals, the VAT breakdown, versions and status are always the server's.
 * No client data library, no local copy to drift, and the frontend calculates nothing.
 *
 * One change at a time: while a change runs, or the page refreshes afterwards, every control
 * that changes something is disabled. FastAPI also locks the transaction row per change, so
 * this is about a clear screen rather than about safety.
 *
 * Stale tabs: a change carries the version it is based on (If-Match), FastAPI refuses an old
 * one without changing anything, and an editor with unsaved edits keeps them and lets the user
 * decide. When the tab becomes visible again and nothing is being edited it refreshes by itself;
 * it never refreshes over an open editor.
 */
export function TransactionEditor({ transaction }: { transaction: Transaction }) {
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const [mutating, setMutating] = useState(false);
  const [editorsOpen, setEditorsOpen] = useState(0);
  const [notice, setNotice] = useState<EditorNotice | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(() => {
    if (!mounted.current) return;
    startRefresh(() => router.refresh());
  }, [router]);

  const announce = useCallback((next: EditorNotice | null) => {
    if (mounted.current) setNotice(next);
  }, []);

  const registerEditor = useCallback(() => {
    setEditorsOpen((count) => count + 1);
    return () => setEditorsOpen((count) => count - 1);
  }, []);

  const mutate = useCallback(
    async <T,>(call: () => Promise<ApiResult<T>>): Promise<ApiResult<T> | null> => {
      if (inFlight.current) return null;
      inFlight.current = true;
      setMutating(true);
      setNotice(null);
      try {
        const result = await call();
        // The user may have left (or switched organization) while this ran. The change itself
        // was not cancelled, but nothing here reacts to its answer any more.
        if (mounted.current && result.ok) refresh();
        return result;
      } finally {
        inFlight.current = false;
        if (mounted.current) setMutating(false);
      }
    },
    [refresh],
  );

  const report = useCallback(
    (failure: Failure, context: "save" | "lifecycle") => {
      const error = (text: string, problems?: string[]): EditorNotice => ({ tone: "error", text, problems });
      switch (failure.kind) {
        case "conflict":
          announce(error(context === "save" ? TEXT.notDraft : failure.message));
          refresh(); // the screen is out of date: show the state FastAPI has
          break;
        case "stale":
          announce(error(TEXT.staleElsewhere));
          refresh();
          break;
        case "gone":
          announce(error(TEXT.gone));
          refresh();
          break;
        case "problems":
          announce(error(failure.message, failure.problems.map((problem) => describeProblem(problem, transaction.lines))));
          break;
        case "validation":
          announce(error([...failure.formErrors, ...Object.values(failure.fieldErrors).flat()].join(" ") || "Some values are not valid."));
          break;
        case "other":
          announce(error(failure.message));
          break;
      }
    },
    [announce, refresh, transaction.lines],
  );

  // A tab that was in the background may show an old transaction. Refresh when it comes back,
  // but never over an editor the user has open, and without polling.
  const onVisible = useEffectEvent(() => {
    if (document.visibilityState === "visible" && editorsOpen === 0 && !inFlight.current) refresh();
  });
  useEffect(() => {
    const listener = () => onVisible();
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  }, []);

  const busy = mutating || refreshing;
  const api = useMemo<EditorApi>(
    () => ({
      transaction,
      readOnly: transaction.status !== "draft",
      busy,
      refreshing,
      editorsOpen,
      notice,
      mutate,
      report,
      announce,
      refresh,
      registerEditor,
    }),
    [transaction, busy, refreshing, editorsOpen, notice, mutate, report, announce, refresh, registerEditor],
  );

  return (
    <EditorContext.Provider value={api}>
      <div data-testid="transaction-editor" data-status={transaction.status} data-busy={busy || undefined} className="flex flex-col gap-5">
        <div className="flex flex-wrap items-center gap-3">
          <TransactionStatusBadge status={transaction.status} />
          {transaction.status === "completed" && <span data-testid="status-note">Completed: finalized and read-only. Reopen it to make changes.</span>}
          {transaction.status === "cancelled" && <span data-testid="status-note">Cancelled: final, kept for the record. It cannot be changed.</span>}
        </div>

        {notice && (
          <Notice tone={notice.tone} testId="editor-notice">
            <p>{notice.text}</p>
            {notice.problems && notice.problems.length > 0 && (
              <ul data-testid="editor-problems" className="mt-1 list-disc pl-5">
                {notice.problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            )}
          </Notice>
        )}

        <LifecycleBar />
        <HeaderEditor />
        <LinesTable />
        {transaction.status === "draft" && <AddLineForm />}
        <TotalsPanel />
      </div>
    </EditorContext.Provider>
  );
}
