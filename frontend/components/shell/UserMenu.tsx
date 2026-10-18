"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import { SignOut } from "@/components/shell/SignOut";
import { useDismiss } from "@/components/shell/use-dismiss";

/** "Fredrik Axö" -> "FA"; a name that is just an address ("fredrik@x.se") -> "F". Decoration only: the full name is in the menu. */
export function initialsOf(name: string, email: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  const source = words.length > 0 ? words : [email];
  return source
    .slice(0, 2)
    .map((word) => Array.from(word)[0]?.toUpperCase() ?? "")
    .join("");
}

/**
 * The person's avatar in the top right as a round button; pressing it opens their menu: who is signed in, the account page and
 * sign out. (The organization switcher is its own drop-down to the left of it.)
 */
export function UserMenu({ name, email, mode }: { name: string; email: string; mode: "dev" | "session" | null }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useDismiss(box, open, () => setOpen(false));

  return (
    <div ref={box} className="relative" data-testid="user-menu">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account menu for ${name || email}`}
        onClick={() => setOpen((now) => !now)}
        data-testid="user-menu-toggle"
        className="flex size-9 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white shadow-xs hover:bg-accent-hover focus-visible:outline-2 focus-visible:outline-offset-2"
      >
        {initialsOf(name, email)}
      </button>
      {/* The menu stays in the page while closed (hidden), so a person's address can always be read by tests and tools. */}
      <div role="menu" aria-label="Account" hidden={!open} className="absolute right-0 z-30 mt-1 w-64 max-w-[90vw] rounded-xl border border-border bg-panel p-1 shadow-lg">
        <div className="px-3 py-2">
          {name && <div className="truncate text-sm font-medium">{name}</div>}
          <div data-testid="user-email" className="truncate text-xs text-muted">
            {email}
          </div>
        </div>
        <div className="my-1 border-t border-border" />
        <a role="menuitem" href="/account" className="flex rounded-lg px-3 py-2 text-sm text-secondary hover:bg-surface-hover hover:text-foreground" data-testid="account-link">
          Account
        </a>
        <Link role="menuitem" href="/support" prefetch={false} className="flex rounded-lg px-3 py-2 text-sm text-secondary hover:bg-surface-hover hover:text-foreground" data-testid="support-link">
          Help and support
        </Link>
        <Link role="menuitem" href="/?home" prefetch={false} className="flex rounded-lg px-3 py-2 text-sm text-secondary hover:bg-surface-hover hover:text-foreground" data-testid="homepage-link">
          Go to homepage
        </Link>
        {mode !== null && (
          <div className="px-1 pt-1">
            <SignOut mode={mode} variant="menu" />
          </div>
        )}
      </div>
    </div>
  );
}
