"use client";

import { createContext, useContext, useEffect } from "react";

import type { ApiResult } from "@/lib/api/errors";
import type { Transaction } from "@/lib/api/types";
import type { Failure } from "@/features/transactions/failures";

/** A message above the editor: what happened, and (for a blocked lifecycle step) the problems. */
export interface EditorNotice {
  tone: "error" | "info";
  text: string;
  problems?: string[];
}

/**
 * What every part of the transaction editor shares. The transaction itself is the SERVER's:
 * it comes from the page (a server component) and is replaced by a refresh after every
 * successful change. Nothing here keeps a copy of the lines or the totals; the editor only
 * holds what the user is typing, what is running, and what to tell the user.
 */
export interface EditorApi {
  /** The authoritative transaction, exactly as the server last sent it. */
  transaction: Transaction;
  /** The server says it is not a draft: nothing can be edited. Presentation only; FastAPI enforces it. */
  readOnly: boolean;
  /** A change is running, or the page is being refreshed. Nothing else may be changed meanwhile. */
  busy: boolean;
  /** The page is re-reading the transaction (totals shown are about to change). */
  refreshing: boolean;
  /** How many row, header or add-line editors are open. Lifecycle buttons wait for zero. */
  editorsOpen: number;
  notice: EditorNotice | null;
  /**
   * Run ONE change at a time. Resolves to null (and does nothing) if another is running. A
   * successful change is followed by a refresh of the transaction. A response that arrives
   * after the editor is gone is ignored.
   */
  mutate<T>(call: () => Promise<ApiResult<T>>): Promise<ApiResult<T> | null>;
  /** Tell the user about a failure the caller does not handle itself (and refresh if the screen is out of date). */
  report(failure: Failure, context: "save" | "lifecycle"): void;
  announce(notice: EditorNotice | null): void;
  refresh(): void;
  /** An editor calls this while it is open; the returned function says it closed. */
  registerEditor(): () => void;
}

export const EditorContext = createContext<EditorApi | null>(null);

export function useEditor(): EditorApi {
  const editor = useContext(EditorContext);
  if (editor === null) throw new Error("useEditor must be used inside <TransactionEditor>");
  return editor;
}

/** Mount this inside anything that is an open editor: while it exists, lifecycle actions wait. */
export function useRegisterEditor(): void {
  const { registerEditor } = useEditor();
  useEffect(() => registerEditor(), [registerEditor]);
}
