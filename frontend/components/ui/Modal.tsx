"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";

/**
 * A pop-up window over the page, built on the browser's own <dialog>: it traps the keyboard focus inside while open, Escape closes it, and
 * the page behind cannot be used until it is closed. A press outside the window (on the dimmed page) closes it too, and so does the ×.
 * What is inside keeps its own state while the window is closed (the content is always mounted), so a half-written form is still there
 * when it is opened again; closing never sends or discards anything by itself.
 */
export function Modal({
  open,
  onClose,
  title,
  children,
  testId,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const wanted = useRef(open);

  useEffect(() => {
    wanted.current = open;
    const element = dialog.current;
    if (element === null) return;
    // (jsdom, used by the unit tests, has no showModal: there the dialog is simply shown with its `open` attribute.)
    if (open && !element.open) {
      if (typeof element.showModal === "function") element.showModal();
      else element.setAttribute("open", "");
    }
    if (!open && element.open) {
      if (typeof element.close === "function") element.close();
      else element.removeAttribute("open");
    }
  }, [open]);

  return (
    <dialog
      ref={dialog}
      aria-labelledby={titleId}
      data-testid={testId}
      // Escape: the browser closes the dialog itself; the page is told so that it forgets it was open. A close the page asked for (another window
      // opening in its place) is not reported back, or it would close that other window too.
      onClose={() => {
        if (wanted.current) onClose();
      }}
      // A press on the dimmed backdrop lands on the dialog element itself, never on its content.
      onClick={(event) => {
        if (event.target === dialog.current) onClose();
      }}
      className="m-auto max-h-[90vh] w-[min(48rem,calc(100vw-2rem))] rounded-xl border border-border bg-panel p-0 text-foreground shadow-xl backdrop:bg-black/50"
    >
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-3">
        <h2 id={titleId} className="text-lg font-semibold">
          {title}
        </h2>
        <button type="button" onClick={onClose} aria-label="Close" data-testid="modal-close" className="rounded px-2 py-1 text-xl leading-none text-muted hover:bg-surface-hover hover:text-foreground">
          ×
        </button>
      </div>
      <div className="max-h-[calc(90vh-4rem)] overflow-y-auto p-5">{children}</div>
    </dialog>
  );
}
