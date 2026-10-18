"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { ICONS } from "@/components/shell/MainNav";
import type { NavItem } from "@/components/shell/nav";

/** The four places used most on a phone sit in the bar; everything else is under "More". */
const BAR_PATHS = ["", "/calendar", "/transactions", "/customers"];

function Icon({ path }: { path: string }) {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d={ICONS[path] ?? ICONS[""]} />
    </svg>
  );
}

function isCurrent(base: string, item: NavItem, pathname: string): boolean {
  const href = `${base}${item.path}`;
  return item.path === "" ? pathname === base : pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * The phone's navigation: a bar at the bottom of the screen within thumb's reach, replacing the long sideways-scrolling menu.
 * Hidden from the medium width up, where the sidebar takes over.
 */
export function MobileTabs({ orgId, items }: { orgId: string; items: NavItem[] }) {
  const pathname = usePathname();
  const base = `/o/${orgId}`;
  const enabled = items.filter((item) => item.enabled);
  const bar = BAR_PATHS.map((path) => enabled.find((item) => item.path === path)).filter((item): item is NavItem => item !== undefined);
  const rest = enabled.filter((item) => !bar.includes(item));
  const moreCurrent = rest.some((item) => isCurrent(base, item, pathname));

  return (
    <nav aria-label="Main (bottom bar)" className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-sidebar pb-[env(safe-area-inset-bottom)] md:hidden" data-testid="mobile-tabs">
      <ul className="grid grid-cols-5">
        {bar.map((item) => {
          const current = isCurrent(base, item, pathname);
          return (
            <li key={item.path}>
              <Link href={`${base}${item.path}`} aria-current={current ? "page" : undefined} className={`flex h-14 flex-col items-center justify-center gap-0.5 text-[11px] ${current ? "font-medium text-accent-icon" : "text-secondary"}`}>
                <Icon path={item.path} />
                {item.label}
              </Link>
            </li>
          );
        })}
        <li>
          {/* Keyed by the address: going to a page closes the sheet. */}
          <More key={pathname} base={base} rest={rest} pathname={pathname} current={moreCurrent} />
        </li>
      </ul>
    </nav>
  );
}

function More({ base, rest, pathname, current }: { base: string; rest: NavItem[]; pathname: string; current: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" aria-expanded={open} aria-controls="mobile-more" onClick={() => setOpen(!open)} className={`flex h-14 w-full flex-col items-center justify-center gap-0.5 text-[11px] ${current || open ? "font-medium text-accent-icon" : "text-secondary"}`}>
        <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
          <path d="M5 12h.01M12 12h.01M19 12h.01" />
        </svg>
        More
      </button>
      {open && (
        <ul id="mobile-more" className="absolute inset-x-0 bottom-full grid max-h-[60vh] grid-cols-2 gap-1 overflow-y-auto border-t border-border bg-sidebar p-3 shadow-lg">
          {rest.map((item) => (
            <li key={item.path}>
              <Link href={`${base}${item.path}`} aria-current={isCurrent(base, item, pathname) ? "page" : undefined} className={`flex h-11 items-center gap-3 rounded-lg px-3 text-sm ${isCurrent(base, item, pathname) ? "bg-accent-soft font-medium" : "text-secondary hover:bg-surface-hover"}`}>
                <Icon path={item.path} />
                {item.label}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
