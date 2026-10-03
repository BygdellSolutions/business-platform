"use client";

import { createContext, useContext, useEffect } from "react";

import type { ApiResult } from "@/lib/api/errors";
import type { Invoice } from "@/lib/api/types";
import type { InvoiceFailure } from "@/features/invoices/failures";

/** A message above the invoice: what happened. */
export interface ViewNotice {
  tone: "error" | "info";
  text: string;
}

/**
 * What every part of the invoice screen shares. The invoice itself is the SERVER's: it comes from
 * the page (a server component) and is replaced by a refresh after every successful change.
 * Nothing here keeps a copy of any amount or line; it only holds what the user is typing, what is
 * running, and what to tell the user.
 */
export interface InvoiceApi {
  /** The authoritative invoice, exactly as the server last sent it. */
  invoice: Invoice;
  /** The user's role may change invoices. Presentation only; FastAPI decides. */
  canMutate: boolean;
  /** A draft the user may change: the only state in which any control that changes something appears. */
  editable: boolean;
  /** A change is running, the page is refreshing, or the state is being checked. Nothing may be changed meanwhile. */
  busy: boolean;
  refreshing: boolean;
  /** An earlier request ended with an unknown outcome and has not been checked yet: no new attempt is offered. */
  unverified: boolean;
  /** How many editors are open. Issue and Delete wait for zero. */
  editorsOpen: number;
  notice: ViewNotice | null;
  /**
   * Run ONE change at a time. Resolves to null (and does nothing) if another is running. A
   * successful change is followed by a refresh unless `refresh: false` (the caller navigates away).
   */
  mutate<T>(call: () => Promise<ApiResult<T>>, options?: { refresh?: boolean }): Promise<ApiResult<T> | null>;
  /** Tell the user about a failure the caller does not handle itself, and bring the screen up to date if it is stale. */
  report(failure: InvoiceFailure): void;
  announce(notice: ViewNotice | null): void;
  refresh(): void;
  /** Re-read the invoice from the backend to learn whether an unconfirmed request took effect. */
  verify(): Promise<void>;
  /** An editor calls this while it is open; the returned function says it closed. */
  registerEditor(): () => void;
  /** False once the screen has been left: nothing may navigate or update because of a late answer. */
  isMounted(): boolean;
}

export const InvoiceContext = createContext<InvoiceApi | null>(null);

export function useInvoice(): InvoiceApi {
  const invoice = useContext(InvoiceContext);
  if (invoice === null) throw new Error("useInvoice must be used inside <InvoiceView>");
  return invoice;
}

/** Mount this inside anything that is an open editor: while it exists, Issue and Delete wait. */
export function useRegisterEditor(): void {
  const { registerEditor } = useInvoice();
  useEffect(() => registerEditor(), [registerEditor]);
}
