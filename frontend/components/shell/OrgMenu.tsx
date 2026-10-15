"use client";

import { useRef, useState } from "react";

import { useDismiss } from "@/components/shell/use-dismiss";
import type { Membership } from "@/lib/api/types";

/**
 * The organization switcher as a drop-down in the top bar: the button shows the current organization, the list shows every
 * organization this person belongs to, and (for those allowed) the way to create a new one. Each entry is a plain link (a FULL
 * navigation, not a client transition): switching organization reloads the page, so no client state, router cache entry or
 * in-flight request from the previous organization can survive.
 */
export function OrgMenu({
  organizations,
  currentId,
  canCreate = false,
  created,
}: {
  organizations: Membership[];
  currentId: string;
  canCreate?: boolean;
  /** How many organizations this account has created and may create ("Created 1 / 1"). */
  created?: { count: number; max: number };
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, open, () => setOpen(false));
  const current = organizations.find((organization) => organization.id === currentId);

  return (
    <div ref={box} className="relative" data-testid="org-switcher">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Switch organization"
        onClick={() => setOpen((now) => !now)}
        data-testid="org-switcher-toggle"
        className="flex h-9 max-w-56 items-center gap-2 rounded-lg border border-border-strong bg-panel px-3 text-sm font-medium hover:bg-surface-hover"
      >
        <span className="truncate" data-testid="org-switcher-current">
          {current?.name ?? "Organization"}
        </span>
        <svg aria-hidden="true" viewBox="0 0 20 20" className={`size-4 shrink-0 text-muted transition-transform ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 8l5 5 5-5" />
        </svg>
      </button>
      {open && (
        <div role="menu" aria-label="Organizations" className="absolute right-0 z-30 mt-1 w-72 max-w-[90vw] rounded-xl border border-border bg-panel p-1 shadow-lg">
          <div className="px-3 py-1.5 text-[11px] font-semibold tracking-wider text-muted uppercase">Your organizations</div>
          {organizations.map((organization) =>
            organization.id === currentId ? (
              <span key={organization.id} role="menuitem" aria-current="true" className="flex items-center justify-between gap-2 rounded-lg bg-accent-soft px-3 py-2 text-sm font-medium text-accent-soft-foreground">
                <span className="truncate">{organization.name}</span>
                <span aria-hidden="true">✓</span>
              </span>
            ) : (
              // A plain anchor on purpose: see the component comment (full navigation).
              <a key={organization.id} role="menuitem" href={`/o/${organization.id}`} className="flex rounded-lg px-3 py-2 text-sm text-secondary hover:bg-surface-hover hover:text-foreground">
                <span className="truncate">{organization.name}</span>
              </a>
            ),
          )}
          {(canCreate || created) && <div className="my-1 border-t border-border" />}
          {created && (
            <div className="px-3 py-1 text-xs text-muted" data-testid="created-count">
              Created {created.count} / {created.max}
            </div>
          )}
          {canCreate && (
            // Presentation only (FastAPI decides). A plain anchor: creation happens outside any organization.
            <a role="menuitem" href="/organizations/new" className="flex rounded-lg px-3 py-2 text-sm text-secondary hover:bg-surface-hover hover:text-foreground" data-testid="create-organization-link">
              + Create organization
            </a>
          )}
        </div>
      )}
    </div>
  );
}
