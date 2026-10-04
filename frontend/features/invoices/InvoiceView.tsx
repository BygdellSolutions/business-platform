"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useEffectEvent, useMemo, useRef, useState, useTransition } from "react";

import { useOrgId } from "@/components/shell/org-context";
import { Notice } from "@/components/ui/Notice";
import { DownloadPdf } from "@/features/invoices/DownloadPdf";
import { DraftHeader } from "@/features/invoices/DraftHeader";
import { TEXT, type InvoiceFailure } from "@/features/invoices/failures";
import { InvoiceActions } from "@/features/invoices/InvoiceActions";
import { InvoiceContext, type InvoiceApi, type ViewNotice } from "@/features/invoices/invoice-context";
import { InvoiceDocument } from "@/features/invoices/InvoiceDocument";
import { apiFetch } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/errors";
import type { Invoice } from "@/lib/api/types";

/**
 * The invoice page's interactive part.
 *
 * Source of truth: the `invoice` prop, read on the server by the page. Every successful change is
 * followed by `router.refresh()`, which re-reads the whole invoice from FastAPI, so the document,
 * its amounts, version and status are always the server's. The frontend calculates nothing.
 *
 * One change at a time. A change carries the version it is based on (If-Match); FastAPI refuses an
 * old one without changing anything, and the screen then shows what is on the server and keeps the
 * user's open draft. A request whose outcome is UNKNOWN (network, server error, timeout) is never
 * retried and never assumed to have failed: the invoice is re-read first, and until that has
 * answered no new attempt is offered. An answer that arrives after the screen was left is ignored.
 *
 * A tab that becomes visible again refreshes itself, but never while an editor is open or a change
 * is running, so it cannot overwrite what the user is typing.
 */
export function InvoiceView({ invoice, canMutate }: { invoice: Invoice; canMutate: boolean }) {
  const orgId = useOrgId();
  const router = useRouter();
  const [refreshing, startRefresh] = useTransition();
  const [mutating, setMutating] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [unverified, setUnverified] = useState(false);
  const [editorsOpen, setEditorsOpen] = useState(0);
  const [notice, setNotice] = useState<ViewNotice | null>(null);
  const inFlight = useRef(false);
  const mounted = useRef(false);
  const latest = useRef(invoice);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    latest.current = invoice;
  }, [invoice]);

  // The invoice was issued elsewhere while this tab still showed a draft with an editor open:
  // say so (the editor closes itself; the draft's edits can no longer be saved anywhere).
  const [seenStatus, setSeenStatus] = useState(invoice.status);
  if (invoice.status !== seenStatus) {
    setSeenStatus(invoice.status);
    if (seenStatus === "draft" && invoice.status === "issued" && editorsOpen > 0) setNotice({ tone: "error", text: TEXT.issuedElsewhere });
  }

  const refresh = useCallback(() => {
    if (!mounted.current) return;
    startRefresh(() => router.refresh());
  }, [router]);

  const announce = useCallback((next: ViewNotice | null) => {
    if (mounted.current) setNotice(next);
  }, []);

  const registerEditor = useCallback(() => {
    setEditorsOpen((count) => count + 1);
    return () => setEditorsOpen((count) => count - 1);
  }, []);

  const mutate = useCallback(
    async <T,>(call: () => Promise<ApiResult<T>>, options: { refresh?: boolean } = {}): Promise<ApiResult<T> | null> => {
      if (inFlight.current) return null;
      inFlight.current = true;
      setMutating(true);
      setNotice(null);
      try {
        const result = await call();
        if (mounted.current && result.ok && options.refresh !== false) refresh();
        return result;
      } finally {
        inFlight.current = false;
        if (mounted.current) setMutating(false);
      }
    },
    [refresh],
  );

  const verify = useCallback(async () => {
    if (!mounted.current) return;
    setVerifying(true);
    try {
      const shownVersion = latest.current.version;
      const shownStatus = latest.current.status;
      const result = await apiFetch<Invoice>(orgId, `/invoices/${latest.current.id}`);
      if (!mounted.current) return;
      if (result.ok) {
        // The authoritative state is known. If it differs from the screen, the screen is refreshed.
        if (result.data.version !== shownVersion || result.data.status !== shownStatus) refresh();
        setUnverified(false);
        setNotice({ tone: "info", text: result.data.status === "issued" && shownStatus === "draft" ? "The invoice was issued. The latest state is shown." : TEXT.verified });
      } else if (result.error.kind === "not_found") {
        setUnverified(false);
        setNotice({ tone: "error", text: TEXT.gone });
        refresh();
      } else {
        setUnverified(true); // still unknown: no new attempt is offered until a check succeeds
        setNotice({ tone: "error", text: `${TEXT.unconfirmed} The check itself failed; try it again.` });
      }
    } finally {
      if (mounted.current) setVerifying(false);
    }
  }, [orgId, refresh]);

  const report = useCallback(
    (failure: InvoiceFailure) => {
      const error = (text: string): ViewNotice => ({ tone: "error", text });
      switch (failure.kind) {
        case "stale":
          announce(error(TEXT.staleElsewhere));
          refresh();
          break;
        case "issued":
          announce(error(TEXT.issuedElsewhere));
          refresh();
          break;
        case "sourceChanged":
          announce(error(TEXT.sourceChanged));
          refresh();
          break;
        case "conflict":
        case "ineligible":
          announce(error(failure.message));
          refresh(); // the screen may be out of date: show the state FastAPI has
          break;
        case "gone":
          announce(error(TEXT.gone));
          refresh();
          break;
        case "validation":
          announce(error([...failure.formErrors, ...Object.values(failure.fieldErrors).flat()].join(" ") || "Some values are not valid."));
          break;
        case "unconfirmed":
          announce({ tone: "info", text: TEXT.unconfirmed });
          setUnverified(true);
          void verify();
          break;
        case "other":
          announce(error(failure.message));
          break;
      }
    },
    [announce, refresh, verify],
  );

  const onVisible = useEffectEvent(() => {
    if (document.visibilityState === "visible" && editorsOpen === 0 && !inFlight.current && !verifying) refresh();
  });
  useEffect(() => {
    const listener = () => onVisible();
    document.addEventListener("visibilitychange", listener);
    return () => document.removeEventListener("visibilitychange", listener);
  }, []);

  const busy = mutating || refreshing || verifying;
  const api = useMemo<InvoiceApi>(
    () => ({
      invoice,
      canMutate,
      editable: canMutate && invoice.status === "draft",
      busy,
      refreshing,
      unverified,
      editorsOpen,
      notice,
      mutate,
      report,
      announce,
      refresh,
      verify,
      registerEditor,
      isMounted: () => mounted.current,
    }),
    [invoice, canMutate, busy, refreshing, unverified, editorsOpen, notice, mutate, report, announce, refresh, verify, registerEditor],
  );

  return (
    <InvoiceContext.Provider value={api}>
      <div data-testid="invoice-view" data-status={invoice.status} data-busy={busy || undefined} className="flex flex-col gap-5">
        {invoice.status === "issued" && (
          <p data-testid="issued-note" className="text-sm">
            Issued and numbered: this invoice is a permanent document and cannot be edited or deleted.
          </p>
        )}
        {notice && (
          <Notice tone={notice.tone} testId="invoice-notice">
            <p>{notice.text}</p>
          </Notice>
        )}
        <DownloadPdf invoice={invoice} />
        <InvoiceActions />
        <DraftHeader />
        <div className={refreshing ? "opacity-50" : ""} aria-busy={refreshing} data-updating={refreshing || undefined}>
          <InvoiceDocument invoice={invoice} orgId={orgId} />
        </div>
      </div>
    </InvoiceContext.Provider>
  );
}
