"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { useDismiss } from "@/components/shell/use-dismiss";
import { apiFetch } from "@/lib/api/client";

export interface SearchPage {
  label: string;
  /** Path below /o/{orgId}. */
  path: string;
}

interface Hit {
  key: string;
  group: string;
  label: string;
  detail?: string;
  href: string;
}

/** What is searched, with the list endpoints that already exist (each one is scoped to the organization by the backend). */
const SOURCES: { group: string; path: string; page: string; hit: (row: Record<string, unknown>) => { label: string; detail?: string } }[] = [
  { group: "Customers", path: "/customers", page: "/customers", hit: (r) => ({ label: String(r.name ?? ""), detail: r.number ? `Customer ${r.number}` : undefined }) },
  { group: "Horses", path: "/horses", page: "/horses", hit: (r) => ({ label: String(r.name ?? ""), detail: r.number ? `Horse ${r.number}` : undefined }) },
  { group: "Products and services", path: "/items", page: "/catalog", hit: (r) => ({ label: String(r.name ?? ""), detail: r.number ? `Item ${r.number}` : undefined }) },
  { group: "Suppliers", path: "/suppliers", page: "/suppliers", hit: (r) => ({ label: String(r.name ?? ""), detail: r.number ? `Supplier ${r.number}` : undefined }) },
  {
    group: "Invoices",
    path: "/invoices",
    page: "/invoices",
    hit: (r) => ({ label: r.number_text ? `Invoice ${r.number_text}` : "Draft invoice", detail: r.customer_name ? String(r.customer_name) : undefined }),
  },
];

const MIN_LENGTH = 2;
const PER_GROUP = 5;
const DELAY_MS = 200;

/**
 * Quick access from the top of every page: type a few letters and go straight to a page of the menu, a customer, a horse, a
 * product, a supplier, an invoice or (a number) an order. The records come from the lists that already exist, each asked for
 * its first few matches, so what is shown is exactly what the person may see there (a refusal just leaves that group out).
 * Press "/" or Ctrl+K to start typing, the arrow keys and Enter to choose, Escape to close.
 */
export function QuickSearch({ orgId, pages }: { orgId: string; pages: SearchPage[] }) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  /** The answer to ONE search text: what an older text found is never shown for a newer one. */
  const [found, setFound] = useState<{ text: string; hits: Hit[] }>({ text: "", hits: [] });
  const [chosen, setChosen] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const close = useCallback(() => setOpen(false), []);
  useDismiss(box, open, close);

  const text = query.trim();
  const pageHits: Hit[] =
    text.length === 0
      ? []
      : pages
          .filter((page) => page.label.toLowerCase().includes(text.toLowerCase()))
          .map((page) => ({ key: `page:${page.path}`, group: "Go to", label: page.label, href: `/o/${orgId}${page.path}` }));

  useEffect(() => {
    if (text.length < MIN_LENGTH) return;
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      const asked = SOURCES.map(async (source) => {
        const result = await apiFetch<Record<string, unknown>[]>(orgId, `${source.path}?q=${encodeURIComponent(text)}&limit=${PER_GROUP}`, { signal: controller.signal });
        if (!result.ok || !Array.isArray(result.data)) return [] as Hit[];
        return result.data.slice(0, PER_GROUP).map((row) => ({ key: `${source.path}:${row.id}`, group: source.group, href: `/o/${orgId}${source.page}/${row.id}`, ...source.hit(row) }));
      });
      if (/^\d{1,18}$/.test(text)) {
        asked.push(
          apiFetch<Record<string, unknown>[]>(orgId, `/transactions?number=${text}&limit=${PER_GROUP}`, { signal: controller.signal }).then((result) =>
            result.ok && Array.isArray(result.data) ? result.data.map((row) => ({ key: `order:${row.id}`, group: "Orders", label: `Order ${row.number}`, href: `/o/${orgId}/transactions/${row.id}` })) : [],
          ),
        );
      }
      try {
        const found = (await Promise.all(asked)).flat();
        if (!controller.signal.aborted) setFound({ text, hits: found });
      } catch {
        /* a cancelled search is not an error to show */
      }
    }, DELAY_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [text, orgId]);

  // "/" or Ctrl+K starts a search from anywhere on the page (not while typing in a field).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const typing = event.target instanceof HTMLElement && (["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName) || event.target.isContentEditable);
      if ((event.key === "/" && !typing && !event.ctrlKey && !event.metaKey) || ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k")) {
        event.preventDefault();
        input.current?.focus();
        setOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const hits = found.text === text ? found.hits : [];
  const searching = text.length >= MIN_LENGTH && found.text !== text;
  const all = [...pageHits, ...hits];
  const active = Math.min(chosen, Math.max(all.length - 1, 0)); // the choice never points past the end of a shorter list

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setOpen(true);
      setChosen(Math.min(active + 1, Math.max(all.length - 1, 0)));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setChosen(Math.max(active - 1, 0));
    } else if (event.key === "Enter" && open && all[active]) {
      event.preventDefault();
      // A full page load, like every other link of the shell.
      window.location.assign(all[active].href);
    } else if (event.key === "Escape") {
      setOpen(false);
      input.current?.blur();
    }
  }

  const groups = all.reduce<{ name: string; hits: Hit[] }[]>((list, hit) => {
    const last = list.find((group) => group.name === hit.group);
    if (last) last.hits.push(hit);
    else list.push({ name: hit.group, hits: [hit] });
    return list;
  }, []);
  const showPanel = open && text.length > 0;

  return (
    <div ref={box} className="relative w-full max-w-xl" data-testid="quick-search" role="search" aria-label="Quick search">
      <svg aria-hidden="true" viewBox="0 0 20 20" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round">
        <circle cx="9" cy="9" r="5.5" />
        <path d="M13.5 13.5L17 17" />
      </svg>
      <input
        ref={input}
        type="search"
        role="combobox"
        aria-label="Quick search"
        aria-expanded={showPanel}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={showPanel && all[active] ? `${listId}-${active}` : undefined}
        autoComplete="off"
        placeholder="Search customers, horses, orders, invoices…  ( / )"
        value={query}
        onChange={(event) => {
          setQuery(event.target.value);
          setChosen(0);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        data-testid="quick-search-input"
        className="h-9 w-full rounded-lg border border-border-strong bg-panel pr-3 pl-9 text-sm placeholder:text-muted"
      />
      {showPanel && (
        <div id={listId} role="listbox" aria-label="Quick search results" data-testid="quick-search-results" className="absolute left-0 z-30 mt-1 max-h-[70vh] w-full overflow-y-auto rounded-xl border border-border bg-panel p-1 shadow-lg">
          {groups.map((group) => (
            <div key={group.name} role="group" aria-label={group.name}>
              <div className="px-3 py-1.5 text-[11px] font-semibold tracking-wider text-muted uppercase">{group.name}</div>
              {group.hits.map((hit) => {
                const index = all.indexOf(hit);
                return (
                  <a
                    key={hit.key}
                    id={`${listId}-${index}`}
                    role="option"
                    aria-selected={index === active}
                    href={hit.href}
                    data-testid="quick-search-hit"
                    onMouseEnter={() => setChosen(index)}
                    className={`flex items-baseline justify-between gap-3 rounded-lg px-3 py-2 text-sm ${index === active ? "bg-accent-soft text-accent-soft-foreground" : "text-secondary hover:bg-surface-hover"}`}
                  >
                    <span className="truncate">{hit.label}</span>
                    {hit.detail && <span className="shrink-0 text-xs text-muted">{hit.detail}</span>}
                  </a>
                );
              })}
            </div>
          ))}
          {all.length === 0 && (
            <p className="px-3 py-3 text-sm text-muted" data-testid="quick-search-empty">
              {text.length < MIN_LENGTH ? `Type at least ${MIN_LENGTH} letters.` : searching ? "Searching…" : "Nothing found."}
            </p>
          )}
          {all.length > 0 && searching && <p className="px-3 py-1 text-xs text-muted">Searching…</p>}
        </div>
      )}
    </div>
  );
}
