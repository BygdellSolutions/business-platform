"use client";

import { usePathname } from "next/navigation";

import type { NavItem } from "@/components/shell/nav";

// Simple outline icons (24×24, stroked) keyed by path; decoration only, the label carries the meaning.
const ICONS: Record<string, string> = {
  "": "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
  "/transactions": "M9 3h6v3H9zM7 4.5H5V21h14V4.5h-2M8 11h8M8 15h5",
  "/invoices": "M6 3h9l4 4v14H6zM14 3v5h5M9 13h7M9 17h5",
  "/customers": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 21a8 8 0 0 1 16 0",
  "/suppliers": "M2 6h12v10H2zM14 9h4l4 4v3h-8M6 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM18 20a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
  "/catalog": "M3 12V3h9l9 9-9 9zM8 8h.01",
  "/inventory": "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10",
  "/horses": "M6 3v9a6 6 0 0 0 12 0V3M6 7h2M16 7h2",
  "/settings": "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4",
  "/members": "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2 20a7 7 0 0 1 14 0M16 4.5a3.5 3.5 0 0 1 0 6.5M18 13.5a7 7 0 0 1 4 6.5",
};

/**
 * The sidebar menu, grouped under section headings, with the current page highlighted. Entries stay plain anchors
 * (as before): a menu click is a full page load.
 */
export function MainNav({ orgId, items }: { orgId: string; items: NavItem[] }) {
  const pathname = usePathname();
  const base = `/o/${orgId}`;
  const sections: { title?: string; items: NavItem[] }[] = [];
  for (const item of items) {
    const last = sections.at(-1);
    if (last && last.title === item.section) last.items.push(item);
    else sections.push({ title: item.section, items: [item] });
  }

  return (
    <nav aria-label="Main" className="flex gap-4 overflow-x-auto text-sm md:flex-col md:overflow-visible">
      {sections.map((section) => (
        <div key={section.title ?? "home"} className="flex shrink-0 flex-col gap-1">
          {section.title && <span className="hidden px-3 text-xs font-semibold tracking-wide text-zinc-500 uppercase md:block">{section.title}</span>}
          <ul className="flex gap-1 md:flex-col">
            {section.items.map((item) => {
              const href = `${base}${item.path}`;
              const current = item.path === "" ? pathname === base : pathname === href || pathname.startsWith(`${href}/`);
              return (
                <li key={item.label}>
                  {item.enabled ? (
                    <a
                      href={href}
                      aria-current={current ? "page" : undefined}
                      className={`flex items-center gap-3 rounded-md px-3 py-2 whitespace-nowrap ${
                        current
                          ? "bg-accent-soft font-semibold text-accent-soft-foreground"
                          : "text-zinc-700 hover:bg-zinc-200/60 dark:text-zinc-300 dark:hover:bg-zinc-800"
                      }`}
                    >
                      <svg aria-hidden="true" viewBox="0 0 24 24" className="size-5 shrink-0" fill="none" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round">
                        <path d={ICONS[item.path] ?? ICONS[""]} />
                      </svg>
                      {item.label}
                    </a>
                  ) : (
                    <span aria-disabled="true" className="flex px-3 py-2 whitespace-nowrap text-zinc-400">
                      {item.label} (coming soon)
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}
